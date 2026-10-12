%global units discbayd.service discbay-island.service discbay-studio.service

Name:           discbay
Version:        0.3.0
Release:        1%{?dist}
Summary:        Console-style disc launcher: insert a CD and your game starts

License:        GPL-3.0-or-later
URL:            https://github.com/jolenjolen/discbay
Source0:        %{url}/archive/v%{version}/%{name}-%{version}.tar.gz

BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  systemd-rpm-macros
BuildRequires:  desktop-file-utils
# For the test suite in %%check
BuildRequires:  python3-gobject
BuildRequires:  python3-flask
BuildRequires:  xorriso

Requires:       python3-gobject
Requires:       gtk4
Requires:       gtk4-layer-shell
Requires:       python3-flask
Requires:       xorriso
Requires:       xdg-utils
# The island plays its insert chirp with pw-play
Recommends:     pipewire-utils

%description
DiscBay turns any CD-R into a launcher disc. Slide it in, hear the chirp as
it spins up, and a notification island slides down with the disc's name and
a short countdown before your game or app starts. Pull the disc out and the
app closes, like a cartridge.

Discs hold no software, only which installed app to open: native apps,
Flatpaks, AppImages, Steam games or any command. DiscBay Studio, a local web
app, picks the app, crops a cover, burns rewritable discs and prints disc
and case-spine labels.

%prep
%autosetup -n %{name}-%{version}

%generate_buildrequires
%pyproject_buildrequires

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files discbay

install -Dpm0755 frontends/gtk4-island/discbay_island.py %{buildroot}%{_bindir}/discbay-island
%py3_shebang_fix %{buildroot}%{_bindir}/discbay-island

install -Dpm0644 -t %{buildroot}%{_userunitdir} systemd/discbayd.service \
    systemd/discbay-island.service systemd/discbay-studio.service
# Switch the services on for every user (takes effect at their next login).
install -Dpm0644 packaging/80-discbay.preset %{buildroot}%{_userpresetdir}/80-discbay.preset

desktop-file-install --dir=%{buildroot}%{_datadir}/applications data/me.jolen.DiscBay.Studio.desktop
install -Dpm0644 data/me.jolen.DiscBay.svg %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/me.jolen.DiscBay.svg

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/me.jolen.DiscBay.Studio.desktop
%pyproject_check_import
%{python3} -m unittest discover -s tests -v

%post
%systemd_user_post %{units}

%preun
%systemd_user_preun %{units}

%postun
%systemd_user_postun_with_restart %{units}

%files -f %{pyproject_files}
%license LICENSE
%doc README.md docs/GUIDE.md docs/PROTOCOL.md examples/discs.toml
%{_bindir}/discbayd
%{_bindir}/discbay-ctl
%{_bindir}/discbay-studio
%{_bindir}/discbay-island
%{_userunitdir}/discbayd.service
%{_userunitdir}/discbay-island.service
%{_userunitdir}/discbay-studio.service
%{_userpresetdir}/80-discbay.preset
%{_datadir}/applications/me.jolen.DiscBay.Studio.desktop
%{_datadir}/icons/hicolor/scalable/apps/me.jolen.DiscBay.svg

%changelog
* Sun Oct 11 2026 Jolen <jolenjolen@users.noreply.github.com> - 0.3.0-1
- First RPM: daemon, notification island, DiscBay Studio, label printing
