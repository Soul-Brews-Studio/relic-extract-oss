"""relic-extract — step 2 of scan -> extract -> embed.

  relic-extract init                 write ~/.arra/relic.toml with the defaults, if it is missing
  relic-extract config               the [extract] settings in effect (file merged over the defaults)
  relic-extract run [--months 2026-09,2026-10] [--stream text] [--force]
                                     relic-scan months -> OUT/<stream>/YYYY-MM.parquet (only what changed)
  relic-extract status               per stream: months cut, chunks, distinct texts, newest ts

  --config FILE                      another config (default ~/.arra/relic.toml, or ARRA_CONFIG)
"""
import json, os, sys, time

from . import config, extract


def _opt(args, name, default=None):
    if name in args:
        i = args.index(name)
        if i + 1 >= len(args):
            sys.exit(f"✗ {name} needs a value")
        return args[i + 1]
    return default


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return
    cmd, rest = args[0], args[1:]
    path = _opt(rest, "--config")
    if cmd == "init":
        p, made = config.init(path)
        print(f"{'wrote' if made else 'kept (already there)'} {p}")
        return
    cfg, p, existed = config.load(path)
    if cmd == "config":
        print(f"# {p}" + ("" if existed else "  (missing: defaults; relic-extract init writes it)"))
        print(json.dumps(cfg, indent=1))
    elif cmd == "run":
        t = time.time()
        months = _opt(rest, "--months")
        stream = _opt(rest, "--stream")
        print(f"relic-extract: {cfg['source']} -> {cfg['out']} · config {p}" + ("" if existed else " (defaults)"))
        done = extract.run(cfg, set(months.split(",")) if months else None, force="--force" in rest,
                           streams={stream} if stream else None)
        print(f"{len(done)} part(s) cut in {time.time() - t:.1f}s")
    elif cmd == "status":
        import duckdb
        con = duckdb.connect()
        for name, st in cfg["streams"].items():
            d = os.path.join(cfg["out"], name)
            parts = sorted(f for f in os.listdir(d) if f.endswith(".parquet")) if os.path.isdir(d) else []
            if not parts:
                print(f"{name:9} {'on ' if st.get('enabled') else 'off'}  no parts")
                continue
            n, u, last = con.execute(f"SELECT count(*), count(DISTINCT key), max(ts) FROM read_parquet('{d}/*.parquet')").fetchone()
            print(f"{name:9} {'on ' if st.get('enabled') else 'off'}  {len(parts)} months · {n:,} chunks · {u:,} distinct texts · newest {last}")
    else:
        sys.exit(f"✗ unknown command {cmd}\n  relic-extract init | config | run | status")


if __name__ == "__main__":
    main()
