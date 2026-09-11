from archlinux:latest

run echo "NoExtract = !*locale*/fr*/* !usr/share/i18n/locales/fr_FR*" >> /etc/pacman.conf

# Two Arch packages here are split, and installing only the half that
# carries the headers gets through configure and then fails the build:
#
#   gwenhywfar-gtk  supplies gwengui-gtk3.pc, which CMakeLists.txt requires
#                   whenever WITH_AQBANKING and WITH_GNUCASH are both on
#                   (the default). The gwenhywfar package now provides only
#                   libgwenhywfar.so, so it no longer brings this in.
#
#   boost-libs      supplies the libboost_*.so.<ver> files. The boost
#                   package is headers and CMake config only and declares
#                   no dependencies at all, so find_package(Boost) succeeds
#                   on the config it ships while the libraries it points at
#                   are absent -- ninja then fails on a missing
#                   libboost_filesystem.so.<ver> with nothing to build it.
run pacman -Syu --quiet --noconfirm glibc gcc cmake make boost boost-libs python3 pkg-config gettext gtk3 guile git ninja gtest gmock sqlite3 webkit2gtk-4.1 swig gwenhywfar gwenhywfar-gtk aqbanking intltool libxslt libofx postgresql-libs libmariadbclient libdbi libdbi-drivers wayland-protocols > /dev/null

run echo en_US.UTF-8 UTF-8 > /etc/locale.gen
run echo en_GB.UTF-8 UTF-8 >> /etc/locale.gen
run echo fr_FR.UTF-8 UTF-8 >> /etc/locale.gen
run locale-gen
copy entrypoint.sh /
entrypoint /entrypoint.sh
