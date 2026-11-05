# Dagster runtime: launch apps + env. dagster itself runs via uv inside dagster/
# (wheels pinned by each uv.lock) - it is not a nix package.
#
# Fixed ports: UI on 3000 everywhere (dg dev and process-compose alike).
# No auth: the dagster OSS webserver has none - nothing to configure.
{ pkgs, ... }:
let
  dagsterDir = "dagster";
  nercachePackage = pkgs.buildGoModule {
    pname = "dakp-nercache";
    version = "0.1.0";
    src = pkgs.lib.cleanSourceWith {
      src = ../../dagster/projects/dakp/workers/go;
      name = "dakp-worker-source";
    };
    subPackages = [ "cmd/dakp-nercache" ];
    vendorHash = "sha256-52v8Z+I+65jRps5ooMJsFoEWZY3Y391ULnw+OE/cRQM=";
    doCheck = false; # nercache tests are run by the Go/Nx test target
  };
  withDagsterHome = ''
    export DAGSTER_HOME="$PWD/${dagsterDir}/.dagster-home"
    mkdir -p "$DAGSTER_HOME"
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

  packages.dagster-units =
    let
      launcher = pkgs.writeShellScript "dakp-dagster-service" ''
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
      cacheLauncher = pkgs.writeShellScript "dakp-nercache-service" ''
        set -euo pipefail
        : "''${DAKP_WORKDIR:?set DAKP_WORKDIR in dagster.env}"
        exec ${pkgs.lib.getExe' nercachePackage "dakp-nercache"} --workdir "$DAKP_WORKDIR"
      '';
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
          unit "DAKP managed NER cache (Pebble)" cacheLauncher ""
        ))
        (pkgs.writeTextDir "share/systemd/user/dagster-daemon.service" (
          unit "DAKP Dagster daemon" "${launcher} daemon"
            "Wants=dakp-nercache.service\nAfter=dakp-nercache.service"
        ))
        (pkgs.writeTextDir "share/systemd/user/dagster-webserver.service" (
          unit "DAKP Dagster webserver" "${launcher} webserver"
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
