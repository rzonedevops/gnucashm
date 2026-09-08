from archlinux:latest

run echo "NoExtract = !*locale*/fr*/* !usr/share/i18n/locales/fr_FR*" >> /etc/pacman.conf

# gwenhywfar-gtk supplies gwengui-gtk3.pc, which CMakeLists.txt requires
# whenever WITH_AQBANKING and WITH_GNUCASH are both on (the default). Arch
# split it out of the gwenhywfar package, which still provides only
# libgwenhywfar.so, so installing gwenhywfar alone no longer brings it in.
run pacman -Syu --quiet --noconfirm glibc gcc cmake make boost python3 pkg-config gettext gtk3 guile git ninja gtest gmock sqlite3 webkit2gtk-4.1 swig gwenhywfar gwenhywfar-gtk aqbanking intltool libxslt libofx postgresql-libs libmariadbclient libdbi libdbi-drivers wayland-protocols > /dev/null

run echo en_US.UTF-8 UTF-8 > /etc/locale.gen
run echo en_GB.UTF-8 UTF-8 >> /etc/locale.gen
run echo fr_FR.UTF-8 UTF-8 >> /etc/locale.gen
run locale-gen
copy entrypoint.sh /
entrypoint /entrypoint.sh
