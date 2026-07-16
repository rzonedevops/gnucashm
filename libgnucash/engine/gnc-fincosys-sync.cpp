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
#include "Split.h"
#include "Transaction.h"
#include "gnc-commodity.h"
#include "gnc-date.h"
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
        if (!parse_value (out))
            return false;
        skip_ws ();
        return eof ();
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

/* gnc_iso8601_to_time64_gmt() requires a full "YYYY-MM-DD HH:MM:SS"-style
 * string; the sync-feed's "date" fields may instead be bare "YYYY-MM-DD"
 * (fincosys's bank-statement extracts don't carry a time-of-day) or use a
 * 'T' separator per RFC 3339. Normalize both into the form the parser
 * accepts, defaulting to midnight when no time-of-day is present. */
time64
parse_syncfeed_datetime (const std::string &date_str)
{
    if (date_str.empty ())
        return 0;

    std::string normalized = date_str;
    auto tpos = normalized.find ('T');
    if (tpos != std::string::npos)
        normalized[tpos] = ' ';
    else if (normalized.find (' ') == std::string::npos)
        normalized += " 00:00:00";

    return gnc_iso8601_to_time64_gmt (normalized.c_str ());
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

        /* Skip organizations without an assigned code: the importer (and
         * fincosys-atomspace-builder's loader) treat "code" as the primary
         * key and skip records without one, so an empty code can never be
         * synced -- exporting it would silently vanish on reimport. */
        const char *org_code = gncOrganizationGetID (org);
        if (org_code == nullptr || *org_code == '\0')
            continue;

        if (!first_org)
            out << ",\n";
        first_org = false;

        out << "    {\n";
        out << "      \"code\": " << json_quote (org_code) << ",\n";
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

            /* As with organization codes above: an account with no usable
             * identifier can't be re-imported, since the importer treats
             * "account_number" as the primary key too. */
            if (acct_number.empty ())
                continue;

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

gint
gnc_transactions_from_syncfeed_json (QofBook *book, const gchar *json)
{
    g_return_val_if_fail (book != nullptr, -1);
    g_return_val_if_fail (json != nullptr, -1);

    JsonValue root;
    JsonParser parser (json);
    if (!parser.parse (root) || !root.is_object ())
    {
        g_warning ("gnc_transactions_from_syncfeed_json: invalid JSON document");
        return -1;
    }

    const JsonValue *accounts = root.find ("accounts");
    const JsonValue *transactions = root.find ("transactions");
    if (transactions == nullptr || !transactions->is_array ())
        return 0;

    gnc_commodity_table *comm_table = gnc_commodity_table_get_table (book);
    Account *root_account = gnc_book_get_root_account (book);

    /* Pass 1: create every account up front, keyed by "code", so that
     * transaction splits can reference accounts regardless of the order
     * they appear in the "accounts" array. */
    std::map<std::string, Account *> accounts_by_code;
    std::map<std::string, std::string> parent_by_code;

    if (accounts != nullptr && accounts->is_array ())
    {
        for (const JsonValue &acct_val : accounts->items ())
        {
            if (!acct_val.is_object ())
                continue;

            std::string code = acct_val.get_string ("code");
            if (code.empty () || accounts_by_code.count (code))
                continue;

            Account *account = xaccMallocAccount (book);
            xaccAccountSetCode (account, code.c_str ());
            xaccAccountSetName (account, acct_val.get_string ("name", code).c_str ());
            xaccAccountSetDescription (account, acct_val.get_string ("description").c_str ());

            GNCAccountType acct_type = ACCT_TYPE_ASSET;
            std::string type_str = acct_val.get_string ("account_type");
            if (!type_str.empty ())
                xaccAccountStringToType (type_str.c_str (), &acct_type);
            xaccAccountSetType (account, acct_type);

            std::string currency = acct_val.get_string ("currency", "ZAR");
            gnc_commodity *comm = gnc_commodity_table_lookup (
                comm_table, GNC_COMMODITY_NS_CURRENCY, currency.c_str ());
            if (comm != nullptr)
                xaccAccountSetCommodity (account, comm);

            accounts_by_code[code] = account;
            parent_by_code[code] = acct_val.get_string ("parent_code");
        }

        /* Pass 2: wire up the account tree now that every code is known,
         * falling back to the book's root account for entries with no
         * "parent_code" (or one that never resolved to an account). */
        for (const auto &entry : accounts_by_code)
        {
            const std::string &code = entry.first;
            Account *account = entry.second;

            auto parent_it = parent_by_code.find (code);
            Account *parent = root_account;
            if (parent_it != parent_by_code.end () && !parent_it->second.empty ())
            {
                auto found = accounts_by_code.find (parent_it->second);
                if (found != accounts_by_code.end ())
                    parent = found->second;
            }
            gnc_account_append_child (parent, account);
        }
    }

    gint count = 0;

    for (const JsonValue &tx_val : transactions->items ())
    {
        if (!tx_val.is_object ())
            continue;

        const JsonValue *splits = tx_val.find ("splits");
        if (splits == nullptr || !splits->is_array ())
            continue;

        std::string currency = tx_val.get_string ("currency", "ZAR");
        gnc_commodity *comm = gnc_commodity_table_lookup (
            comm_table, GNC_COMMODITY_NS_CURRENCY, currency.c_str ());

        Transaction *trans = xaccMallocTransaction (book);
        xaccTransBeginEdit (trans);

        if (comm != nullptr)
            xaccTransSetCurrency (trans, comm);
        xaccTransSetDescription (trans, tx_val.get_string ("description").c_str ());
        xaccTransSetNum (trans, tx_val.get_string ("txid").c_str ());
        xaccTransSetDatePostedSecsNormalized (
            trans, parse_syncfeed_datetime (tx_val.get_string ("date")));

        int splits_added = 0;
        for (const JsonValue &split_val : splits->items ())
        {
            if (!split_val.is_object ())
                continue;

            std::string account_code = split_val.get_string ("account_code");
            auto found = accounts_by_code.find (account_code);
            if (found == accounts_by_code.end ())
            {
                /* Split references an account not present in this
                 * document's "accounts" array -- skip it rather than
                 * aborting the whole transaction; a partial/unbalanced
                 * import is still useful for manual reconciliation. */
                g_warning ("gnc_transactions_from_syncfeed_json: split "
                           "references unknown account_code '%s', skipping",
                           account_code.c_str ());
                continue;
            }

            Split *split = xaccMallocSplit (book);
            xaccSplitSetParent (split, trans);
            xaccSplitSetAccount (split, found->second);
            xaccSplitSetMemo (split, split_val.get_string ("memo").c_str ());

            gnc_numeric amount = double_to_gnc_numeric (
                split_val.get_number ("amount", 0.0), GNC_DENOM_AUTO,
                GNC_HOW_DENOM_REDUCE | GNC_HOW_RND_NEVER);
            xaccSplitSetValue (split, amount);
            xaccSplitSetAmount (split, amount);
            ++splits_added;
        }

        xaccTransCommitEdit (trans);

        if (splits_added > 0)
            ++count;
    }

    return count;
}
