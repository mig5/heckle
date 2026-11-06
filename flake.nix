{
  description = "Heckle: turn existing forge configuration into maintainable Terraform or OpenTofu";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
        inherit (pkgs) lib;
        python3Packages = pkgs.python3Packages;

        heckle = python3Packages.buildPythonApplication {
          pname = "heckle";
          version = (lib.importTOML ./pyproject.toml).project.version;
          pyproject = true;

          # Only what the wheel and the test suite need, so edits to docs,
          # examples or packaging for other distros don't rebuild heckle.
          # release.sh and tests.sh are exercised by tests/test_release_commands.py.
          src = lib.fileset.toSource {
            root = ./.;
            fileset = lib.fileset.unions [
              ./pyproject.toml
              ./README.md
              ./LICENSE
              ./src
              ./tests
              ./release.sh
              ./tests.sh
            ];
          };

          build-system = [ python3Packages.poetry-core ];
          # Heckle has no runtime Python dependencies. It needs tofu or
          # terraform at run time, which it takes from --tf or PATH.
          dependencies = [ ];

          # Tests run under `nix flake check` (checks.default below), not on every
          # build, so consumers such as dev shells don't run the suite on install.
          doCheck = false;
          nativeCheckInputs = [ python3Packages.pytestCheckHook ];
          pythonImportsCheck = [ "heckle" ];

          meta = {
            description = "Turn existing GitHub, GitLab, Gitea and Forgejo configuration into maintainable Terraform or OpenTofu";
            homepage = "https://heckle.tf";
            license = lib.licenses.gpl3Plus;
            mainProgram = "heckle";
            platforms = lib.platforms.unix;
          };
        };
      in
      {
        packages = {
          inherit heckle;
          default = heckle;
        };
        # `nix flake check` builds the package with its test suite.
        checks.default = heckle.overridePythonAttrs (_: { doCheck = true; });
      });
}
