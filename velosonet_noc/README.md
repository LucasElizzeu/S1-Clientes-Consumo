# Monitor 4G/5G VELOSONET — Tefé (6 BBUs Huawei)

## Arquitetura
```
velosonet/
  config.py          carrega settings.yaml, bbus.yaml, .env; logging com redação de segredos
  bbu_session.py     sessão Playwright por BBU, lock por BBU, login/expiração/reconexão, MML somente-leitura, keep-alive
  mml_parser.py      parse + validação (RETCODE, contagem, END) + extração configurável (sem dupla contagem)
  collector.py       coleta MML + Grafana por BBU; falha isolada por equipamento
  grafana.py         API oficial (/api/dashboards/uid + /api/ds/query), último valor real, checagem de frescor
  report.py          PNG estilo NOC + resumo operacional
  telegram_sender.py envio com retry/backoff e anti-duplicidade
  db.py              SQLite: runs (slot horário único), measurements, deliveries, events
  app.py             APScheduler: Rotina A (10 min) e Rotina B (horário cheio, America/Manaus)
  simulate.py        modo simulação (PNG marcado "DADOS FICTÍCIOS")
config/ settings.yaml  bbus.yaml     tests/     deploy/
```

## Instalação
```
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt && playwright install chromium
cp .env.example .env    # preencha credenciais
python -m velosonet simulate        # valida o PNG sem acessar nada
python -m pytest                    # testes dos parsers
python -m velosonet check-config    # lista o que falta configurar
```

## Fluxo para ativar com dados reais (nada é presumido)
1. Preencha `url` de cada BBU em `config/bbus.yaml`.
2. `python -m velosonet snapshot --bbu 1` → gera screenshot/HTML da tela de login em `data/samples/`; com isso definimos os seletores.
3. Defina os seletores (login, menu MML, campo de comando, botão executar, área de saída).
4. `python -m velosonet capture --bbu 1 --command "DSP S1INTERFACE"` (e `DSP NRCELLUENUMBER` nas BBUs 1/3/4) → resposta bruta real.
5. Com a resposta real, defina o `parser` em `settings.yaml` (`field`, `mode`, `dedupe_by`). **Enquanto `unconfigured`, o sistema não inventa números**: a célula aparece como FALHA.
6. Preencha `grafana` por BBU (uid do dashboard, id do painel, métrica download/upload/agregado, `scale` para Mb/s) e valide uma vez contra a tela do Grafana.
7. `python -m velosonet once` (ciclo único) e depois `python -m velosonet run` (serviço).

## Regras implementadas
- Somente `DSP`/`LST` (bloqueio por regex; `MOD/ADD/RST/...` e múltiplos comandos são rejeitados).
- 5G = **N/A** nas BBUs 2/5/6; falha de consulta **nunca** vira 0 (FALHA / INDISP. / 0 são distintos).
- Keep-alive só quando ocioso, nunca simultâneo à coleta (lock), com pausa após falhas de login (evita bloqueio de conta).
- Sem execução duplicada: slot horário único no SQLite + lock + `max_instances=1`; reinício retoma slots interrompidos; entregas pendentes são reenviadas.
- TLS sempre verificado por padrão (`verify_tls: true`).
- Segredos só em `.env`; filtro de log mascara qualquer valor de variável *PASS/*TOKEN.

## Execução automática
Linux: `deploy/velosonet.service` (systemd: `systemctl enable --now velosonet`). Windows: `deploy/windows_install.md`.

## Limitações conhecidas
- 4G: soma de `S1 Interface User Number` (DSP S1INTERFACE). 5G: soma de `Total RRC Number` da tabela por célula do DSP NRCELLUENUMBER (a 2ª tabela, por operadora, é ignorada para não dobrar). Confirme se esse é o campo que você considera 'usuário 5G'.
- Se o Grafana só oferecer gráficos sem consulta via API, a captura visual com Playwright não foi implementada (evita estimar valores por imagem).
- Validado aqui: parsers (com respostas sintéticas), banco, gerador de PNG e modo simulação. Não validado: Playwright contra BBU real, Grafana e Telegram (sem rede/equipamentos neste ambiente).

## Certificado autoassinado das BBUs (erro net::ERR_CERT_AUTHORITY_INVALID)
As BBUs redirecionam para https com certificado próprio. Sem desligar a verificação TLS:
1. `python -m velosonet tls-pin --all` (lê o certificado de cada BBU e mostra `tls_spki_sha256`).
2. Cole o valor de cada BBU em `config/bbus.yaml` (`tls_spki_sha256: "..."`). `verify_tls` continua `true`.
3. O Chromium passa a aceitar SOMENTE essas chaves públicas; qualquer outro certificado inválido segue recusado.
Se a BBU for reinstalada/atualizada e o certificado mudar, o login volta a falhar com o mesmo erro: rode o passo 1 de novo.
