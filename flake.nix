{
  description = "graph-monorepo: Nx + Dagster multi-KG monorepo";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
    flake-parts.url = "github:hercules-ci/flake-parts";
    # provides the pi coding agent (same input as /etc/nixos)
    agent-of-empires.url = "github:agent-of-empires/agent-of-empires";
  };

  # binary-cache hits instead of source builds
  nixConfig = {
    extra-substituters = [
      "https://cache.nixos.org"
      "https://nix-community.cachix.org"
      "https://agent-of-empires.cachix.org"
    ];
    extra-trusted-public-keys = [
      "cache.nixos.org-1:6NCHdD59X431o0gWZboiP5epOVrvVPtryaT8MUvsiFA="
      "nix-community.cachix.org-1:mBVF2oxPjuOC0j2PcNZQBzzoSQAAeKPdkNipC9hibio="
      "agent-of-empires.cachix.org-1:Z+VwTlT8GT7giWN9HhJ+Am0DPGfbFVlafcQioBqJ6wY="
    ];
  };

  outputs =
    inputs:
    inputs.flake-parts.lib.mkFlake { inherit inputs; } (
      { self, ... }:
      {
        systems = [ "x86_64-linux" ];
        perSystem =
          { pkgs, ... }:
          {
            imports = [
              ./nix/modules/devshell.nix
              ./nix/modules/dagster.nix
            ];
            formatter = pkgs.nixfmt-tree;
            # cheap eval-time check so `nix flake check` has something to assert
            checks.flake = pkgs.runCommand "graph-monorepo-flake-check" { } "touch $out";
          };
      }
    );
}
