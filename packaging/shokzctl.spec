%global app_id org.example.Shokzctl
%global pypi_version 0.1.0.dev0

Name:           shokzctl
Version:        0.1.0~dev0
Release:        1%{?dist}
Summary:        Manage Shokz OpenRun Pro 2 headset settings

License:        AGPL-3.0-or-later
# Local build only: the source archive comes from `uv build --sdist` (packaging/build-rpm.sh).
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
Command line tool and GTK app for a Shokz OpenRun Pro 2 connected to Fedora.
Reads battery, firmware and EQ over the headset's management channel and
changes EQ presets, custom EQ and multipoint where verified for the headset's
firmware. Not affiliated with Shokz.

%prep
%autosetup -n %{name}-%{pypi_version}

%generate_buildrequires
%pyproject_buildrequires

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files -l shokzctl
install -Dpm 0644 data/%{app_id}.desktop %{buildroot}%{_datadir}/applications/%{app_id}.desktop
install -Dpm 0644 data/%{app_id}.metainfo.xml %{buildroot}%{_metainfodir}/%{app_id}.metainfo.xml
install -Dpm 0644 data/icons/hicolor/scalable/apps/%{app_id}.svg \
    %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/%{app_id}.svg

%check
# Offline only: no hardware, no display, no network.
%pyproject_check_import -e 'shokzctl.ui.app' -e 'shokzctl.ui.window'
%pytest -m "not hardware and not gui"
desktop-file-validate %{buildroot}%{_datadir}/applications/%{app_id}.desktop
# No homepage exists for this local-only app; every other check stays strict.
appstreamcli validate --no-net --override=url-homepage-missing=info %{buildroot}%{_metainfodir}/%{app_id}.metainfo.xml

%files -f %{pyproject_files}
%doc README.md
%{_bindir}/shokzctl
%{_bindir}/shokzctl-gui
%{_datadir}/applications/%{app_id}.desktop
%{_metainfodir}/%{app_id}.metainfo.xml
%{_datadir}/icons/hicolor/scalable/apps/%{app_id}.svg

%changelog
* Fri Nov 13 2026 Markos - 0.1.0~dev0-1
- First local build
