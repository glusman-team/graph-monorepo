# The one devshell: every tool for every language in this repo.
# Enter with `nix develop` (or cd in with direnv via .envrc).
{ pkgs, inputs', ... }:
{
  devShells.default = pkgs.mkShell {
    packages = [
      # python (dagster itself runs via uv inside dagster/, wheels from uv.lock)
      pkgs.uv

      # node (nx task graph; nx itself comes from npm, not nixpkgs)
      pkgs.nodejs_24

      # go workers
      pkgs.go
      pkgs.gopls
      pkgs.golangci-lint

      # jvm/scala seam (mill; unused until a scala worker lands, kept ready)
      pkgs.temurin-bin-21
      pkgs.mill

      # repo tooling
      pkgs.git
      pkgs.gh
      pkgs.prek
      pkgs.nixfmt-tree
      pkgs.actionlint
      pkgs.quicktype

      # pi coding agent (project skills in .pi/ merge with global after trust)
      # inputs' is system-implicit under flake-parts: packages.<name>, no system segment
      inputs'.agent-of-empires.packages.aoe-with-web
    ];

    env = {
      # workers build with the nix-provided toolchain only
      GOTOOLCHAIN = "local";
    };

    shellHook = ''
      # self-heal the committed agent-harness symlinks
      [ -e CLAUDE.md ] || ln -s AGENTS.md CLAUDE.md
      mkdir -p .claude
      [ -e .claude/skills ] || ln -s ../.pi/skills .claude/skills

      # two-stage prek hooks (fast pre-commit, heavy pre-push)
      prek install >/dev/null 2>&1 || echo "prek install skipped (run 'make hooks')"

      echo "graph-monorepo devshell ready: make help"
    '';
  };
}
