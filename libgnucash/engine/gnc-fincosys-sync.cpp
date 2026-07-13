/********************************************************************\
 * gnc-fincosys-sync.cpp -- Fincosys ecosystem sync bridge           *
 * Copyright 2026 GnuCash Contributors                                *
 *                                                                    *
 * This program is free software; you can redistribute it and/or      *
 * modify it under the terms of the GNU General Public License as     *
 * published by the Free Software Foundation; either version 2 of     *
 * the License, or (at your option) any later version.                *
 *                                                                    *
 * This program is distributed in the hope that it will be useful,    *
 * but WITHOUT ANY WARRANTY; without even the implied warranty of     *
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the      *
 * GNU General Public License for more details.                       *
 *********************************************************************/

#include "gnc-fincosys-sync.h"

#include "Account.h"
#include "gnc-commodity.h"
#include "gnc-numeric.h"
#include "qofinstance.h"

#include <cctype>
#include <cstring>
#include <map>
#include <sstream>
#include <string>
#include <vector>

namespace
{

/*
 * Minimal JSON value type + parser, scoped to the shape of the Fincosys
 * Ecosystem Sync Schema v1 (nested objects/arrays of strings, numbers,
 * booleans and null -- see fincosys-atomspace-builder's README). This is
 * intentionally not a general-purpose JSON library.
 */
class JsonValue
{
public:
    enum class Type { Null, Bool, Number, String, Array, Object };

    JsonValue () : m_type (Type::Null) {}

    static JsonValue make_object () { JsonValue v; v.m_type = Type::Object; return v; }
    static JsonValue make_array () { JsonValue v; v.m_type = Type::Array; return v; }
    static JsonValue make_string (std::string s)
    {
        JsonValue v;
        v.m_type = Type::String;
        v.m_string = std::move (s);
        return v;
    }
    static JsonValue make_number (double d)
    {
        JsonValue v;
        v.m_type = Type::Number;
        v.m_number = d;
        return v;
    }
    static JsonValue make_bool (bool b)
    {
        JsonValue v;
        v.m_type = Type::Bool;
        v.m_bool = b;
        return v;
    }

    Type type () const { return m_type; }
    bool is_object () const { return m_type == Type::Object; }
    bool is_array () const { return m_type == Type::Array; }

    const std::vector<JsonValue> &items () const { return m_array; }
    void push_back (JsonValue val) { m_array.push_back (std::move (val)); }

    void set (const std::string &key, JsonValue val) { m_object[key] = std::move (val); }

    const JsonValue *find (const std::string &key) const
    {
        auto it = m_object.find (key);
        return it == m_object.end () ? nullptr : &it->second;
    }

    std::string get_string (const std::string &key, const std::string &def = "") const
    {
        auto *v = find (key);
        return (v && v->m_type == Type::String) ? v->m_string : def;
    }

    double get_number (const std::string &key, double def = 0.0) const
    {
        auto *v = find (key);
        return (v && v->m_type == Type::Number) ? v->m_number : def;
    }

    bool get_bool (const std::string &key, bool def = false) const
    {
        auto *v = find (key);
        return (v && v->m_type == Type::Bool) ? v->m_bool : def;
    }

private:
    Type m_type;
    std::string m_string;
    double m_number = 0.0;
    bool m_bool = false;
    std::vector<JsonValue> m_array;
    std::map<std::string, JsonValue> m_object;
};

class JsonParser
{
public:
    /* Takes ownership of a copy of the text -- storing a reference here
     * would dangle, since callers typically construct this from a
     * temporary std::string built from a const gchar*, and reference
     * lifetime extension does not propagate through a constructor. */
    explicit JsonParser (std::string text) : m_text (std::move (text)), m_pos (0) {}

    bool parse (JsonValue &out)
    {
        skip_ws ();
        return parse_value (out);
    }

private:
    std::string m_text;
    size_t m_pos;

    void skip_ws ()
    {
        while (m_pos < m_text.size () && std::isspace (static_cast<unsigned char> (m_text[m_pos])))
            ++m_pos;
    }

    bool eof () const { return m_pos >= m_text.size (); }
    char peek () const { return m_text[m_pos]; }

    bool consume (char c)
    {
        skip_ws ();
        if (eof () || m_text[m_pos] != c)
            return false;
        ++m_pos;
        return true;
    }

    bool literal (const char *lit)
    {
        size_t len = std::strlen (lit);
        if (m_text.compare (m_pos, len, lit) == 0)
        {
            m_pos += len;
            return true;
        }
        return false;
    }

    bool parse_value (JsonValue &out)
    {
        skip_ws ();
        if (eof ())
            return false;

        switch (peek ())
        {
            case '{':
                return parse_object (out);
            case '[':
                return parse_array (out);
            case '"':
            {
                std::string s;
                if (!parse_string (s))
                    return false;
                out = JsonValue::make_string (std::move (s));
                return true;
            }
            case 't':
                if (!literal ("true"))
                    return false;
                out = JsonValue::make_bool (true);
                return true;
            case 'f':
                if (!literal ("false"))
                    return false;
                out = JsonValue::make_bool (false);
                return true;
            case 'n':
                if (!literal ("null"))
                    return false;
                out = JsonValue ();
                return true;
            default:
                return parse_number (out);
        }
    }

    bool parse_object (JsonValue &out)
    {
        if (!consume ('{'))
            return false;
        out = JsonValue::make_object ();
        skip_ws ();
        if (consume ('}'))
            return true;

        for (;;)
        {
            std::string key;
            skip_ws ();
            if (!parse_string (key))
                return false;
            if (!consume (':'))
                return false;

            JsonValue val;
            if (!parse_value (val))
                return false;
            out.set (key, std::move (val));

            skip_ws ();
            if (consume (','))
                continue;
            if (consume ('}'))
                break;
            return false;
        }
        return true;
    }

    bool parse_array (JsonValue &out)
    {
        if (!consume ('['))
            return false;
        out = JsonValue::make_array ();
        skip_ws ();
        if (consume (']'))
            return true;

        for (;;)
        {
            JsonValue val;
            if (!parse_value (val))
                return false;
            out.push_back (std::move (val));

            skip_ws ();
            if (consume (','))
                continue;
            if (consume (']'))
                break;
            return false;
        }
        return true;
    }

    bool parse_string (std::string &out)
    {
        if (!consume ('"'))
            return false;
        out.clear ();

        while (!eof () && m_text[m_pos] != '"')
        {
            char c = m_text[m_pos++];
            if (c == '\\' && !eof ())
            {
                char esc = m_text[m_pos++];
                switch (esc)
                {
                    case '"': out += '"'; break;
                    case '\\': out += '\\'; break;
                    case '/': out += '/'; break;
                    case 'n': out += '\n'; break;
                    case 't': out += '\t'; break;
                    case 'r': out += '\r'; break;
                    case 'b': out += '\b'; break;
                    case 'f': out += '\f'; break;
                    case 'u':
                        /* Minimal support: the sync schema doesn't use
                         * non-ASCII codes/keys in practice, so \u escapes
                         * are skipped rather than decoded. */
                        if (m_pos + 4 <= m_text.size ())
                            m_pos += 4;
                        out += '?';
                        break;
                    default:
                        out += esc;
                        break;
                }
            }
            else
            {
                out += c;
            }
        }
        if (eof ())
            return false;
        ++m_pos; /* closing quote */
        return true;
    }

    bool parse_number (JsonValue &out)
    {
        size_t start = m_pos;
        if (!eof () && (peek () == '-' || peek () == '+'))
            ++m_pos;
        while (!eof () &&
               (std::isdigit (static_cast<unsigned char> (peek ())) || peek () == '.' ||
                peek () == 'e' || peek () == 'E' || peek () == '+' || peek () == '-'))
            ++m_pos;
        if (m_pos == start)
            return false;

        std::string numstr = m_text.substr (start, m_pos - start);
        try
        {
            out = JsonValue::make_number (std::stod (numstr));
        }
        catch (...)
        {
            return false;
        }
        return true;
    }
};

std::string
json_escape (const std::string &s)
{
    std::string out;
    out.reserve (s.size () + 8);
    for (unsigned char c : s)
    {
        switch (c)
        {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\t': out += "\\t"; break;
            case '\r': out += "\\r"; break;
            default:
                if (c < 0x20)
                {
                    char buf[8];
                    g_snprintf (buf, sizeof (buf), "\\u%04x", c);
                    out += buf;
                }
                else
                {
                    out += static_cast<char> (c);
                }
        }
    }
    return out;
}

std::string
json_quote (const char *s)
{
    return "\"" + json_escape (s ? s : "") + "\"";
}

const char *
account_currency_mnemonic (const Account *account)
{
    gnc_commodity *comm = xaccAccountGetCommodity (account);
    if (comm == nullptr)
        return "ZAR";
    const char *mnemonic = gnc_commodity_get_mnemonic (comm);
    return mnemonic ? mnemonic : "ZAR";
}

} // namespace

gchar *
gnc_organizations_to_fincosys_json (GList *organizations)
{
    if (organizations == nullptr)
        return nullptr;

    std::ostringstream out;
    out << "{\n";
    out << "  \"schema\": \"fincosys-ecosystem-sync/v1\",\n";
    out << "  \"source\": \"gnucashm\",\n";
    out << "  \"organizations\": [\n";

    bool first_org = true;
    for (GList *node = organizations; node; node = node->next)
    {
        auto *org = static_cast<GncOrganization *> (node->data);
        if (org == nullptr)
            continue;

        if (!first_org)
            out << ",\n";
        first_org = false;

        out << "    {\n";
        out << "      \"code\": " << json_quote (gncOrganizationGetID (org)) << ",\n";
        out << "      \"name\": " << json_quote (gncOrganizationGetName (org)) << ",\n";
        out << "      \"active\": " << (gncOrganizationGetActive (org) ? "true" : "false") << ",\n";
        out << "      \"accounts\": [";

        /* gncOrganizationGetEntities() returns the organization's internal
         * GList -- it must not be freed here. */
        GList *entities = gncOrganizationGetEntities (org);
        bool first_acct = true;
        for (GList *enode = entities; enode; enode = enode->next)
        {
            auto *inst = static_cast<QofInstance *> (enode->data);
            if (inst == nullptr || !GNC_IS_ACCOUNT (inst))
                continue;

            auto *account = GNC_ACCOUNT (inst);
            const char *acct_code = xaccAccountGetCode (account);
            const char *acct_name = xaccAccountGetName (account);
            std::string acct_number = (acct_code && *acct_code) ? acct_code
                                                                 : (acct_name ? acct_name : "");

            out << (first_acct ? "\n" : ",\n");
            first_acct = false;

            out << "        {\n";
            out << "          \"account_number\": " << json_quote (acct_number.c_str ()) << ",\n";
            out << "          \"account_name\": " << json_quote (acct_name) << ",\n";
            out << "          \"account_type\": "
                << json_quote (xaccAccountTypeEnumAsString (xaccAccountGetType (account))) << ",\n";
            out << "          \"balance\": "
                << gnc_numeric_to_double (xaccAccountGetBalance (account)) << ",\n";
            out << "          \"currency\": " << json_quote (account_currency_mnemonic (account)) << "\n";
            out << "        }";
        }
        if (!first_acct)
            out << "\n      ";
        out << "]\n";
        out << "    }";
    }

    out << "\n  ]\n";
    out << "}\n";

    return g_strdup (out.str ().c_str ());
}

gint
gnc_organizations_from_fincosys_json (QofBook *book, const gchar *json)
{
    g_return_val_if_fail (book != nullptr, -1);
    g_return_val_if_fail (json != nullptr, -1);

    JsonValue root;
    JsonParser parser (json);
    if (!parser.parse (root) || !root.is_object ())
    {
        g_warning ("gnc_organizations_from_fincosys_json: invalid JSON document");
        return -1;
    }

    const JsonValue *orgs = root.find ("organizations");
    if (orgs == nullptr || !orgs->is_array ())
        return 0;

    gnc_commodity_table *comm_table = gnc_commodity_table_get_table (book);
    gint count = 0;

    for (const JsonValue &org_val : orgs->items ())
    {
        if (!org_val.is_object ())
            continue;

        std::string code = org_val.get_string ("code");
        if (code.empty ())
            continue;

        GncOrganization *org = gncOrganizationCreate (book);
        gncOrganizationSetID (org, code.c_str ());
        gncOrganizationSetName (org, org_val.get_string ("name", code).c_str ());
        gncOrganizationSetActive (org, org_val.get_bool ("active", true));

        const JsonValue *accounts = org_val.find ("accounts");
        if (accounts != nullptr && accounts->is_array ())
        {
            for (const JsonValue &acct_val : accounts->items ())
            {
                if (!acct_val.is_object ())
                    continue;

                std::string acct_number = acct_val.get_string ("account_number");
                if (acct_number.empty ())
                    continue;

                Account *account = xaccMallocAccount (book);
                xaccAccountSetCode (account, acct_number.c_str ());
                xaccAccountSetName (
                    account, acct_val.get_string ("account_name", acct_number).c_str ());

                GNCAccountType acct_type = ACCT_TYPE_BANK;
                std::string type_str = acct_val.get_string ("account_type");
                if (!type_str.empty ())
                    xaccAccountStringToType (type_str.c_str (), &acct_type);
                xaccAccountSetType (account, acct_type);

                std::string currency = acct_val.get_string ("currency", "ZAR");
                gnc_commodity *comm =
                    gnc_commodity_table_lookup (comm_table, GNC_COMMODITY_NS_CURRENCY, currency.c_str ());
                if (comm != nullptr)
                    xaccAccountSetCommodity (account, comm);

                /* GnuCash balances are derived from splits, not set
                 * directly -- record the synced balance as a note so it's
                 * visible for reconciliation against fincosys/gnucashm. */
                std::ostringstream notes;
                notes << "Synced from fincosys-ecosystem-sync/v1: balance="
                      << acct_val.get_number ("balance", 0.0);
                xaccAccountSetNotes (account, notes.str ().c_str ());

                gncOrganizationAddEntity (org, QOF_INSTANCE (account));
            }
        }

        ++count;
    }

    return count;
}
