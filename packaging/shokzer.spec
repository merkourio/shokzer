%global app_id pm.markos.Shokzer
%global pypi_version 0.1.0

Name:           shokzer
Version:        0.1.0
Release:        1%{?dist}
Summary:        Settings for the Shokz OpenRun Pro 2

License:        AGPL-3.0-or-later
URL:            https://github.com/merkourio/shokzer
# Made by packaging/build-rpm.sh or .copr/Makefile (git archive of the tagged tree).
Source0:        %{name}-%{pypi_version}.tar.gz

BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  pyproject-rpm-macros
BuildRequires:  desktop-file-utils
BuildRequires:  appstream
# For %%check: the offline test suite.
BuildRequires:  python3-pytest
BuildRequires:  python3-pytest-asyncio

Requires:       bluez
# The GUI uses the system GTK stack.
Requires:       python3-gobject
Requires:       gtk4
Requires:       libadwaita

%description
Shokzer changes the settings of a Shokz OpenRun Pro 2 from Linux: EQ presets,
a five-band custom EQ, multipoint and connected devices, plus battery and
firmware. It has a GTK app and a command-line tool, shokzer. Community project,
not affiliated with Shokz.

%prep
%autosetup -n %{name}-%{pypi_version}

%generate_buildrequires
%pyproject_buildrequires

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files -l shokzer
install -Dpm 0644 data/%{app_id}.desktop %{buildroot}%{_datadir}/applications/%{app_id}.desktop
install -Dpm 0644 data/%{app_id}.metainfo.xml %{buildroot}%{_metainfodir}/%{app_id}.metainfo.xml
install -Dpm 0644 data/icons/hicolor/scalable/apps/%{app_id}.svg \
    %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/%{app_id}.svg

%check
# Offline only: no hardware, no display, no network.
%pyproject_check_import -e 'shokzer.ui.app' -e 'shokzer.ui.window'
%pytest -m "not hardware and not gui"
desktop-file-validate %{buildroot}%{_datadir}/applications/%{app_id}.desktop
appstreamcli validate --no-net %{buildroot}%{_metainfodir}/%{app_id}.metainfo.xml

%files -f %{pyproject_files}
%doc README.md
%{_bindir}/shokzer
%{_bindir}/shokzer-gui
%{_datadir}/applications/%{app_id}.desktop
%{_metainfodir}/%{app_id}.metainfo.xml
%{_datadir}/icons/hicolor/scalable/apps/%{app_id}.svg

%changelog
* Fri Nov 13 2026 Markos Merkouris <hello@markos.pm> - 0.1.0-1
- First release
