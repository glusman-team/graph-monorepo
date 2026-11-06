# Two shells share the same repository toolchain and activation behavior:
# - default: interactive development, including the pi coding agent.
# - deploy: CI and production, without interactive-only agent dependencies.
{
  pkgs,
  inputs',
  config,
  ...
}:
let
  sharedPackages = [
    # python (dagster itself runs via uv inside dagster/, wheels from uv.lock)
    pkgs.uv

    # node (nx task graph; nx itself comes from npm, not nixpkgs)
    pkgs.nodejs_24

    # go workers
    pkgs.go
    pkgs.gopls
    pkgs.golangci-lint
    pkgs.govulncheck

    # Phoenix LiveView (BEAM/OTP 28)
    pkgs.beam28Packages.elixir_1_19

    # documentation, container, deployment, and shell quality gates
    pkgs.lychee
    pkgs.hadolint
    pkgs.flyctl
    pkgs.shellcheck

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

    # Production tmux supervisor; activates only when dagster.env exists.
    config.packages.dagster-tmux
  ];

  sharedShell = {
    env = {
      # workers build with the nix-provided toolchain only
      GOTOOLCHAIN = "local";
      GOMAXPROCS = "4";
      GOFLAGS = "-p=4";
      ERL_FLAGS = "+S 4:4";
      ELIXIR_ERL_OPTIONS = "+fnu";
      LANG = "C.UTF-8";
      LC_ALL = "C.UTF-8";
    };

    shellHook = ''
      # nix develop may start in a component directory; hooks and symlinks belong at the root.
      repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
      if [ -f "$repo_root/flake.nix" ]; then
        (
          cd "$repo_root"
          { [ -e CLAUDE.md ] || [ -L CLAUDE.md ]; } || ln -s AGENTS.md CLAUDE.md
          mkdir -p .claude
          { [ -e .claude/skills ] || [ -L .claude/skills ]; } || ln -s ../.pi/skills .claude/skills
          prek install >/dev/null 2>&1 || echo "prek install skipped (run 'make hooks')"
        )
      fi

      # No sudo/linger needed: a configured production host starts detached
      # services on shell entry. Other checkouts are unaffected.
      auto_start="''${DAKP_TMUX_AUTOSTART:-auto}"
      if [[ ( "$auto_start" == 1 || ( "$auto_start" == auto && $- == *i* ) ) && \
            -f "''${DAKP_SERVICE_ENV:-$HOME/.config/dagster/dagster.env}" ]]; then
        if ! dakp-services up; then
          # Explicit non-interactive activation must fail the requested command.
          [[ "$auto_start" == 1 ]] && exit 1
          echo "DAKP service auto-start failed; run 'dakp-services status'" >&2
        fi
      fi

      echo "graph-monorepo devshell ready: make help"
    '';
  };
in
{
  devShells = {
    default = pkgs.mkShell (
      sharedShell
      // {
        packages = sharedPackages ++ [
          # pi coding agent (project skills in .pi/ merge with global after trust)
          # inputs' is system-implicit under flake-parts: packages.<name>, no system segment
          inputs'.agent-of-empires.packages.aoe-with-web
        ];
      }
    );

    deploy = pkgs.mkShell (
      sharedShell
      // {
        packages = sharedPackages;
      }
    );
  };
}
