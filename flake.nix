{
  description = "Message bridge between different IM services";
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";
    flake-utils.url = "github:numtide/flake-utils";

    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, flake-utils, uv2nix, pyproject-nix, pyproject-build-systems }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        inherit (nixpkgs) lib;
        pkgs = nixpkgs.legacyPackages.${system};

        workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./.; };
        overlay = workspace.mkPyprojectOverlay {
          sourcePreference = "wheel";
        };

        pyprojectOverrides = final: prev: {
          pyaes = prev.pyaes.overrideAttrs (old: {
            nativeBuildInputs = old.nativeBuildInputs ++ [
              (final.resolveBuildSystem { setuptools = []; })
            ];
          });
          docopt = prev.docopt.overrideAttrs (old: {
            nativeBuildInputs = old.nativeBuildInputs ++ [
              (final.resolveBuildSystem { setuptools = []; })
            ];
          });
        };

        python = pkgs.python312;
        pythonSet = (pkgs.callPackage pyproject-nix.build.packages { inherit python; }).overrideScope(
          lib.composeManyExtensions [
            pyproject-build-systems.overlays.default
            overlay
            pyprojectOverrides
          ]);
      in
      {
        packages.bridge = pythonSet.mkVirtualEnv "bridge-env" workspace.deps.default;
        packages.default = self.packages.${system}.bridge;

        devShells.default = pkgs.mkShell {
          packages = [ pkgs.uv python ];
          env =
            {
              # Prevent uv from managing Python downloads
              UV_PYTHON_DOWNLOADS = "never";
              # Force uv to use nixpkgs Python interpreter
              UV_PYTHON = python.interpreter;
            }
            // lib.optionalAttrs pkgs.stdenv.isLinux {
              # Python libraries often load native shared objects using dlopen(3).
              # Setting LD_LIBRARY_PATH makes the dynamic library loader aware of libraries without using RPATH for lookup.
              # LD_LIBRARY_PATH = lib.makeLibraryPath pkgs.pythonManylinuxPackages.manylinux1;
            };
          shellHook = ''
            unset PYTHONPATH
          '';
        };
      }
    ) // {
      nixosModules.default = { config, lib, ... }:
      with lib;
      let
        pkgs = nixpkgs.legacyPackages."x86_64-linux";
        cfg = config.svkmpn.services.bridge;
        confFile = pkgs.writeText "bridge.toml" ''
          ${cfg.conf}
        '';
      in
      {
        options.svkmpn.services.bridge = {
          enable = mkEnableOption "Enable the bridge";
          conf = mkOption {
            type = types.lines;
            description = "Bot configuration.";
          };
          dataDir = mkOption {
            type = types.str;
            default = "/data/bridge";
            description = "Bot data directory.";
          };
          user = mkOption {
            type = types.str;
            default = "bridge";
            description = "Bot user.";
          };
        };

        config = mkIf cfg.enable {
          users.groups.${cfg.user} = {};
          users.users.${cfg.user} = {
            createHome = true;
            description = "bridge bot";
            isSystemUser = true;
            group = "${cfg.user}";
            home = "${cfg.dataDir}";
          };

          systemd.services."svkmpn.bridge" = {
            wantedBy = [ "multi-user.target" ];
            after = [ "network.target" ];
            serviceConfig = let pkg = self.packages."x86_64-linux".default; in
            {
              ExecStart = "${pkg}/bin/tsbridge -c ${confFile}";
              User = "${cfg.user}";
              Group = "${cfg.user}";
              WorkingDirectory = "${cfg.dataDir}";
            };
          };
        };
      };
    };
}
