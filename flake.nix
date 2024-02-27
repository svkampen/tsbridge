{
  description = "Message bridge between different IM services";
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-23.11";
    flake-utils.url = "github:numtide/flake-utils";
    poetry2nix = {
      url = "github:nix-community/poetry2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  output = { self, nixpkgs, flake-utils, poetry2nix }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        inherit (poetry2nix.lib.mkPoetry2Nix { inherit pkgs; }) mkPoetryApplication defaultPoetryOverrides;
      in
      {
        packages = {
          bridge = mkPoetryApplication {
            projectDir = ./.;
          };

          default = self.packages.${system}.bridge;
        };

        devShells.default = pkgs.mkShell {
          inputsFrom = [ self.packages.${system}.bridge ];
        };
      }
    ) // {
      nixosModules.default = { config, lib, ... }:
      with lib;
      let
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
          users.users.${cfg.user} = {
            createHome = true;
            description = "bridge bot";
            isSystemUser = true;
            group = ${cfg.user};
            home = ${cfg.dataDir};
          };

          systemd.services."svkmpn.bridge" = {
            wantedBy = [ "multi-user.target" ];
            after = [ "network.target" ];
            serviceConfig = let pkg = self.packages.${system}.default; in
            {
              ExecStart = "${pkg}/bin/bridge";
              User = ${cfg.user};
              Group = ${cfg.user};
              WorkingDirectory = ${cfg.dataDir};
            };
          };
        };
      };
    };
}
