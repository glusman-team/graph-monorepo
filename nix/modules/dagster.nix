# Dagster runtime: launch apps + env. dagster itself runs via uv inside dagster/
# (wheels pinned by each uv.lock) - it is not a nix package.
#
# Fixed ports: UI on 3000 everywhere (dg dev and process-compose alike).
# No auth: the dagster OSS webserver has none - nothing to configure.
{ pkgs, inputs', ... }:
let
  go = inputs'.nixpkgs-go.legacyPackages.go_1_26;
  dagsterDir = "dagster";
  nercachePackage = (pkgs.buildGoModule.override { inherit go; }) {
    pname = "dakp-nercache";
    version = "0.1.0";
    src = pkgs.lib.cleanSourceWith {
      src = ../../dagster/projects/dakp/workers/go;
      name = "dakp-worker-source";
    };
    subPackages = [ "cmd/dakp-nercache" ];
    vendorHash = "sha256-pJhvOuYqnRsMQ2wGdMzisZJ2Oii1EUUR8omjziasYHI=";
    doCheck = false; # nercache tests are run by the Go/Nx test target
  };
  withDagsterHome = ''
    export DAGSTER_HOME="$PWD/${dagsterDir}/.dagster-home"
    mkdir -p "$DAGSTER_HOME"
  '';
  dagsterLauncher = pkgs.writeShellScript "dakp-dagster-service" ''
    set -euo pipefail
    : "''${DAKP_REPO_ROOT:?set DAKP_REPO_ROOT in dagster.env}"
    : "''${DAGSTER_HOME:?set a persistent DAGSTER_HOME in dagster.env}"
    export PATH="${pkgs.uv}/bin:${pkgs.git}/bin:$HOME/.nix-profile/bin:/usr/bin:/bin:$PATH"
    mkdir -p "$DAGSTER_HOME"
    cd "$DAKP_REPO_ROOT/dagster/deployments/local"
    case "$1" in
      daemon) exec ${pkgs.uv}/bin/uv run --no-sync dagster-daemon run -w ../../workspace.yaml ;;
      webserver) exec ${pkgs.uv}/bin/uv run --no-sync dagster-webserver -w ../../workspace.yaml -h 127.0.0.1 -p "''${DAKP_DAGSTER_PORT:-3000}" ;;
      *) echo "unknown dagster service: $1" >&2; exit 2 ;;
    esac
  '';
  nercacheLauncher = pkgs.writeShellScript "dakp-nercache-service" ''
    set -euo pipefail
    : "''${DAKP_WORKDIR:?set DAKP_WORKDIR in dagster.env}"
    exec ${pkgs.lib.getExe' nercachePackage "dakp-nercache"} --workdir "$DAKP_WORKDIR"
  '';
in
{
  # dev loop: dg dev loads every projects/* as a separate code location.
  # uv needs a project env to resolve `dg`: deployments/local is the dg command
  # environment, and dg walks up from it to dagster/dg.toml for the workspace.
  apps.dagster-dev = {
    type = "app";
    program = toString (
      pkgs.writeShellScript "dagster-dev" ''
        set -euo pipefail
        ${withDagsterHome}
        cd ${dagsterDir}/deployments/local
        exec ${pkgs.uv}/bin/uv run dg dev
      ''
    );
  };

  # Production cache daemon. Use the same Go module as the extraction worker.
  packages.dakp-nercache = nercachePackage;

  packages.dagster-tmux =
    let
      pc = "${pkgs.process-compose}/bin/process-compose";
      composeConfig = pkgs.writeText "dakp-process-compose.yaml" ''
        version: "0.5"
        log_level: info
        log_length: 10000
        log_location: "''${DAGSTER_HOME}/tmux/services.log"
        log_configuration:
          rotation:
            max_size_mb: 10
            max_age_days: 30
            max_backups: 2
            compress: true
          disable_json: true
        processes:
          nercache:
            command: "${nercacheLauncher}"
            availability: &service
              restart: always
              backoff_seconds: 10
            shutdown: &shutdown
              signal: 15
              timeout_seconds: 120
          daemon:
            command: "${dagsterLauncher} daemon"
            availability: *service
            shutdown: *shutdown
            depends_on:
              nercache:
                condition: process_started
          webserver:
            command: "${dagsterLauncher} webserver"
            availability: *service
            shutdown: *shutdown
            depends_on:
              nercache:
                condition: process_started
      '';
      composeLauncher = pkgs.writeShellScript "dakp-tmux-processes" ''
        set -euo pipefail
        env_file="''${DAKP_SERVICE_ENV:-$HOME/.config/dagster/dagster.env}"
        if [[ ! -r "$env_file" ]]; then
          echo "missing Dagster environment: $env_file" >&2
          exit 1
        fi
        set -a
        . "$env_file"
        set +a
        for path in "$DAKP_REPO_ROOT" "$DAGSTER_HOME" "$DAKP_WORKDIR"; do
          [[ "$path" == /* ]] || {
            echo "production paths must be absolute: $path" >&2
            exit 1
          }
        done
        mkdir -p "$DAGSTER_HOME/tmux"
        chmod 700 "$DAGSTER_HOME/tmux"
        export PC_SOCKET_PATH="$DAGSTER_HOME/tmux/process-compose.sock"
        export PC_LOG_FILE="$DAGSTER_HOME/tmux/process-compose.log"
        cd "$DAKP_REPO_ROOT"
        exec ${pc} --config ${composeConfig} --disable-dotenv --use-uds up
      '';
    in
    pkgs.writeShellApplication {
      name = "dakp-services";
      runtimeInputs = [
        pkgs.coreutils
        pkgs.tmux
        pkgs.util-linux
      ];
      text = ''
        session="dakp"
        target="=$session"
        env_file="''${DAKP_SERVICE_ENV:-$HOME/.config/dagster/dagster.env}"
        pc="${pc}"

        load_config() {
          if [[ ! -r "$env_file" ]]; then
            echo "missing Dagster environment: $env_file" >&2
            return 1
          fi
          set -a
          # shellcheck disable=SC1090
          . "$env_file"
          set +a
          : "''${DAKP_REPO_ROOT:?set DAKP_REPO_ROOT in dagster.env}"
          : "''${DAGSTER_HOME:?set DAGSTER_HOME in dagster.env}"
          : "''${DAKP_WORKDIR:?set DAKP_WORKDIR in dagster.env}"
          for path in "$DAKP_REPO_ROOT" "$DAGSTER_HOME" "$DAKP_WORKDIR"; do
            [[ "$path" == /* ]] || {
              echo "production paths must be absolute: $path" >&2
              return 1
            }
          done
          mkdir -p "$DAGSTER_HOME/tmux"
          chmod 700 "$DAGSTER_HOME/tmux"
          export PC_SOCKET_PATH="$DAGSTER_HOME/tmux/process-compose.sock"
          export PC_LOG_FILE="$DAGSTER_HOME/tmux/process-compose.log"
          cd "$DAKP_REPO_ROOT"
        }

        running() {
          tmux has-session -t "$target" 2>/dev/null
        }

        acquire_lock() {
          mkdir -p "''${XDG_STATE_HOME:-$HOME/.local/state}"
          exec {lock_fd}>"''${XDG_STATE_HOME:-$HOME/.local/state}/dakp-tmux.lock"
          if ! flock -w 15 "$lock_fd"; then
            echo "another dakp-services operation is still in progress" >&2
            return 1
          fi
        }

        pc_command() {
          load_config
          "$pc" --use-uds "$@"
        }

        status() {
          if ! running; then
            echo "DAKP tmux session $session is not running" >&2
            return 1
          fi
          pc_command list --output wide
        }

        check_no_systemd() {
          if command -v systemctl >/dev/null 2>&1; then
            for unit in dakp-nercache dagster-daemon dagster-webserver; do
              if systemctl --user is-active --quiet "$unit" 2>/dev/null || \
                systemctl --user is-enabled --quiet "$unit" 2>/dev/null; then
                echo "$unit is active or enabled; stop and disable the systemd units before using tmux" >&2
                return 1
              fi
            done
          fi
        }

        wait_ready() {
          local dead
          for _ in {1..30}; do
            dead="$(tmux display-message -p -t "$target:services" '#{pane_dead}')"
            if [[ "$dead" == 1 ]]; then
              tmux capture-pane -p -t "$target:services" -S -50 >&2
              return 1
            fi
            if [[ -S "$PC_SOCKET_PATH" ]] && \
              timeout 2 "$pc" --use-uds list --output json >/dev/null 2>&1; then
              return 0
            fi
            sleep 1
          done
          echo "process-compose did not become ready within 30 seconds" >&2
          tmux capture-pane -p -t "$target:services" -S -50 >&2 || true
          return 1
        }

        up() {
          acquire_lock
          load_config
          check_no_systemd
          if running; then
            tmux set-environment -t "$target" DAKP_SERVICE_ENV "$env_file"
            if [[ "$(tmux display-message -p -t "$target:services" '#{pane_dead}')" == 1 ]]; then
              tmux respawn-pane -k -t "$target:services" "${composeLauncher}"
            fi
            wait_ready
            echo "DAKP tmux session $session is already running"
            return 0
          fi
          # Create an idle pane first so remain-on-exit is set before the
          # orchestrator can fail (for example, because of a bad env file).
          # A new tmux server must not inherit the lock, or every pane would
          # hold it forever after this command exits.
          tmux new-session -d -s "$session" -n services {lock_fd}>&-
          tmux set-option -t "$target:services" remain-on-exit on
          tmux set-environment -t "$target" DAKP_SERVICE_ENV "$env_file"
          tmux respawn-pane -k -t "$target:services" "${composeLauncher}"
          wait_ready
          echo "started DAKP services in tmux session $session"
        }

        down() {
          acquire_lock
          if running; then
            if ! load_config || ! timeout 130 "$pc" --use-uds down; then
              # Fall back to the pane's process group if the control socket is bad.
              local pid
              pid="$(tmux display-message -p -t "$target:services" '#{pane_pid}')"
              kill -TERM -- "-$pid" 2>/dev/null || true
              for _ in {1..120}; do
                kill -0 -- "-$pid" 2>/dev/null || break
                sleep 1
              done
              kill -KILL -- "-$pid" 2>/dev/null || true
            fi
            tmux kill-session -t "$target"
            echo "stopped DAKP tmux session $session"
          fi
        }

        case "''${1:-status}" in
          up) up ;;
          down) down ;;
          restart) "$0" down; sleep 2; exec "$0" up ;;
          status) status ;;
          attach) exec tmux attach-session -t "$target" ;;
          logs)
            if [[ -n "''${2:-}" ]]; then
              case "$2" in
                nercache|daemon|webserver) ;;
                *) echo "unknown service: $2" >&2; exit 2 ;;
              esac
              pc_command process logs --follow "$2"
            else
              pc_command process logs --follow
            fi
            ;;
          *) echo "usage: dakp-services {up|down|restart|status|attach|logs [name]}" >&2; exit 2 ;;
        esac
      '';
    };

  packages.dagster-units =
    let
      unit = name: command: after: ''
        [Unit]
        Description=${name}
        ${after}
        [Service]
        Type=simple
        EnvironmentFile=%h/.config/dagster/dagster.env
        ExecStart=${command}
        Restart=always
        RestartSec=10
        TimeoutStopSec=120
        KillMode=control-group
        [Install]
        WantedBy=default.target
      '';
    in
    pkgs.symlinkJoin {
      name = "dagster-units";
      paths = [
        (pkgs.writeTextDir "share/systemd/user/dakp-nercache.service" (
          unit "DAKP managed NER cache (Pebble)" nercacheLauncher ""
        ))
        (pkgs.writeTextDir "share/systemd/user/dagster-daemon.service" (
          unit "DAKP Dagster daemon" "${dagsterLauncher} daemon"
            "Wants=dakp-nercache.service\nAfter=dakp-nercache.service"
        ))
        (pkgs.writeTextDir "share/systemd/user/dagster-webserver.service" (
          unit "DAKP Dagster webserver" "${dagsterLauncher} webserver"
            "Wants=dakp-nercache.service\nAfter=dakp-nercache.service"
        ))
      ];
    };

  # deployable runtime: daemon + webserver under process-compose, same ports.
  # Both run from the dg command env (the only uv project env with dagster
  # installed); workspace.yaml points each location at its own project venv.
  # Ubuntu systemd units can be generated from this same config later.
  packages.processes =
    let
      processComposeYaml = pkgs.writeText "process-compose.yaml" ''
        version: "0.5"
        processes:
          dagster-daemon:
            command: "uv run dagster-daemon run -w ../../workspace.yaml"
            working_dir: "${dagsterDir}/deployments/local"
          dagster-webserver:
            command: "uv run dagster-webserver -w ../../workspace.yaml -h 0.0.0.0 -p 3000"
            working_dir: "${dagsterDir}/deployments/local"
      '';
    in
    pkgs.writeShellScriptBin "processes" ''
      set -euo pipefail
      ${withDagsterHome}
      exec ${pkgs.process-compose}/bin/process-compose --config ${processComposeYaml} up
    '';
}
