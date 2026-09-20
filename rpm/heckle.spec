%{!?upstream_version:%global upstream_version 0.1.1}

Name:           heckle
Version:        %{upstream_version}
Release:        1%{?dist}
Summary:        Generate modular Terraform or OpenTofu from existing software forges
License:        GPL-3.0-or-later
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch
BuildRequires:  pyproject-rpm-macros
BuildRequires:  python3-devel >= 3.11
BuildRequires:  python3-poetry-core
BuildRequires:  python3-pytest
Requires:       python3 >= 3.11

%description
Inventory GitHub, GitLab, Gitea and Forgejo configuration and generate standalone
Terraform or OpenTofu projects. Coverage is provider-specific. Terraform/OpenTofu is an external runtime
requirement for generation and validation, not inventory or coverage reporting.

%prep
%autosetup

%generate_buildrequires
%pyproject_buildrequires

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files heckle

%check
%pytest

%files -f %{pyproject_files}
%license LICENSE
%doc README.md docs/ADOPTION.md CHANGELOG.md SECURITY.md
%{_bindir}/heckle

%changelog
* Mon Sep 21 2026 Miguel Jacq <mig@mig5.net> - 0.1.1-1
- Remove 'validate' subcommand which didn't add much value.

* Sun Sep 20 2026 Miguel Jacq <mig@mig5.net> - 0.1.0-1
- Provider compatibility and security audit release.

* Sun Sep 20 2026 Miguel Jacq <mig@mig5.net> - 0.1.0~alpha13-1
- Audit all GitLab 19.3.0 resource families managed by Heckle.
- Normalize invalid empty values and coupled GitLab settings.
- Improve retained-workdir diagnostics and provider concurrency.

* Sun Sep 20 2026 Miguel Jacq <mig@mig5.net> - 0.1.0~alpha12-1
- Treat empty GitLab reviewer_assignment_strategy as unset.
- Report the actual failed Terraform/OpenTofu command with --keep-workdir.
- Enable parallel requests in the generated GitHub provider configuration.

* Sun Sep 20 2026 Miguel Jacq <mig@mig5.net> - 0.1.0~alpha10-1
- Rename provider-drift terminology to known provider behaviour.
- Make adoption reconciliation an explicit operator decision.

* Sun Sep 20 2026 Miguel Jacq <mig@mig5.net> - 0.1.0~alpha7-1
- Recognise only provider behaviour guarded by a disabled parent feature.
- Keep state-only adoption and reject unknown or effective live changes.

* Sat Sep 19 2026 Miguel Jacq <mig@mig5.net> - 0.1.0~alpha6-1
- Safe state-only adoption fallback for provider import/default conflicts.

* Sat Sep 19 2026 Miguel Jacq <mig@mig5.net> - 0.1.0~alpha5-1
- Presence-aware import rendering and no-change adoption safety gate
