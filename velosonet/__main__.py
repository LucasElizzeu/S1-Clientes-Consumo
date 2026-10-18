import argparse, asyncio, logging, sys
from pathlib import Path
from .config import load_settings, setup_logging, ROOT
from .errors import SelectorsNotConfigured

def main():
    ap = argparse.ArgumentParser(prog="velosonet")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="serviço contínuo (Rotinas A e B)")
    sub.add_parser("once", help="executa a Rotina B uma vez e sai")
    sub.add_parser("simulate", help="gera PNG fictício (sem acessar nada)")
    sub.add_parser("check-config", help="lista pendências de configuração")
    p = sub.add_parser("tls-pin", help="lê o certificado da BBU e mostra o SPKI pin para o bbus.yaml")
    p.add_argument("--bbu", type=int); p.add_argument("--all", action="store_true")
    sub.add_parser("grafana-panels", help="lista painéis do dashboard (id, título, unidade, queries) para conferir o mapeamento")
    p = sub.add_parser("snapshot", help="captura tela de login da BBU p/ mapear seletores"); p.add_argument("--bbu", type=int, required=True)
    p = sub.add_parser("capture", help="executa 1 comando DSP/LST e salva a resposta bruta"); p.add_argument("--bbu", type=int, required=True); p.add_argument("--command", required=True)
    a = ap.parse_args()
    setup_logging(); s = load_settings(); log = logging.getLogger("velosonet")

    if a.cmd == "simulate":
        from .simulate import run
        png, cap = run(s, ROOT / "data" / "simulation" / "simulacao.png"); print(png); print(cap); return
    if a.cmd == "tls-pin":
        from .tlspin import fetch_cert_der, spki_pin_from_der, describe, host_port
        targets = s.bbus if a.all else [b for b in s.bbus if b.id == a.bbu]
        if not targets: print("use --bbu N ou --all"); return
        for b in targets:
            try:
                host, port = host_port(b.url)
                der = fetch_cert_der(host, port); d = describe(der)
                print(f"\nBBU {b.id} {b.site} ({host}:{port})\n  emissor : {d['issuer']}\n  sujeito : {d['subject']}\n"
                      f"  validade: {d['not_before']} -> {d['not_after']}\n  autoassinado: {d['self_signed']}\n"
                      f"  SHA-256 do certificado: {d['sha256_cert']}\n  >>> tls_spki_sha256: \"{spki_pin_from_der(der)}\"")
            except Exception as e:
                print(f"\nBBU {b.id}: falhou ({type(e).__name__}: {e})")
        print("\nConfira o certificado (idealmente na própria BBU) antes de colar o valor no bbus.yaml dessa BBU."); return
    if a.cmd == "check-config":
        from .bbu_session import REQUIRED
        for b in s.bbus:
            miss = [k for k in REQUIRED if not b.selectors.get(k)] + ([] if b.url else ["url"])
            g = [k for k in ("dashboard_uid", "metric") if b.grafana.get(k) in (None, "")]
            if not (b.grafana.get("panel_title") or b.grafana.get("panel_id")): g.append("panel_title/panel_id")
            if b.grafana.get("metric") == "não confirmado": g.append("metric (não confirmado: download/upload/agregado?)")
            print(f"BBU {b.id} {b.site}: faltam {miss or 'nada'} | grafana: {g or 'ok'} | tls_pin: {'ok' if b.tls_spki_sha256 else 'não definido'}")
        for k in ("users_4g", "users_5g"):
            print(k, "parser:", s["commands"][k]["parser"]["mode"]); 
        return
    if a.cmd == "grafana-panels":
        asyncio.run(_panels(s)); return
    asyncio.run(_async(a, s, log))

async def _panels(s):
    from .grafana import GrafanaClient
    import json
    g = GrafanaClient()
    try:
        for p in await g.list_panels(s.bbus[0].grafana["dashboard_uid"]):
            print(f"id={p['id']:<4} {p['type']:<12} unidade={p['unit']!s:<8} {p['title']}")
            for t in p["targets"]: print("      ", json.dumps(t, ensure_ascii=False)[:300])
    finally:
        await g.close()

async def _async(a, s, log):
    from .app import App
    from .config import ROOT
    app = App(s); await app.start()
    try:
        if a.cmd == "snapshot":
            out = ROOT / "data" / "samples"; out.mkdir(parents=True, exist_ok=True)
            await app.sessions[a.bbu].snapshot_login_page(out); print("salvo em", out)
        elif a.cmd == "capture":
            out = ROOT / "data" / "samples"; out.mkdir(parents=True, exist_ok=True)
            s.raw["login_max_retries"] = 1                     # no teste, falhar rápido e mostrar o motivo
            app.sessions[a.bbu].trace_dir = out / "trace"      # screenshots + HTML de cada etapa
            try:
                txt = await app.sessions[a.bbu].run_mml(a.command)
            except Exception as e:
                print(f"\nERRO: {type(e).__name__}: {e}")
                for f in sorted((out / "trace").glob(f"bbu{a.bbu}_error*_info.txt")):
                    print(f"--- {f.name}\n{f.read_text(encoding='utf-8')}")
                print("\nArquivos de diagnóstico em", out / "trace"); return
            
            if txt is None:
                print("ATENCAO: run_mml devolveu None (resposta nao encontrada pela slice_response)")
                print("Verifique data/samples/trace/ — os frames do resultado ficam em bbu1_4_response_frame*.html")
                return
            f = out / f"bbu{a.bbu}_{a.command.replace(' ', '_')}.txt"; f.write_text(txt, encoding="utf-8"); print("salvo em", f)

        elif a.cmd == "once":
            await app.routine_b()
        elif a.cmd == "run":
            sch = app.schedule(); sch.start(); log.info("serviço iniciado (America/Manaus)")
            await app.routine_a()
            await asyncio.Event().wait()
    finally:
        await app.stop()

if __name__ == "__main__":
    try: main()
    except KeyboardInterrupt: sys.exit(0)
