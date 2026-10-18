# Windows — execução automática (NSSM)
1. `python -m venv .venv && .venv\Scripts\pip install -r requirements.txt && .venv\Scripts\playwright install chromium`
2. Baixe o NSSM (nssm.cc) e execute:
```
nssm install VelosonetNOC "C:\velosonet_noc\.venv\Scripts\python.exe" "-m velosonet run"
nssm set VelosonetNOC AppDirectory C:\velosonet_noc
nssm set VelosonetNOC Start SERVICE_AUTO_START
nssm set VelosonetNOC AppExit Default Restart
nssm start VelosonetNOC
```
Alternativa sem NSSM: Agendador de Tarefas → "Ao iniciar o sistema", executar `python -m velosonet run`, marcar "Reiniciar em caso de falha".
