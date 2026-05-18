# Monitor Anatel para OCD

Script Python que verifica diariamente as listas de **Atos** publicados pela
Anatel no portal de Legislacao e envia por email um relatorio com:

- **Atos novos** — apareceram desde a ultima execucao
- **Atos atualizados** — conteudo da pagina mudou ou titulo mudou
- **Atos revogados** — pagina passou a exibir a tag `AtoRevogado`

Categorias monitoradas por padrao (foco em OCD):

- Atos de Certificacao de Produtos
- Atos de Requisitos Tecnicos de Gestao do Espectro
- Atos de Numeracao

Para adicionar Resolucoes, Sumulas, Portarias Normativas etc., edite o
dicionario `CATEGORIES` no topo de `monitor_anatel.py`.

---

## 1. Como funciona

1. Em cada execucao, baixa a lista de atos do **ano corrente** (em janeiro
   tambem o ano anterior) para cada categoria.
2. Para atos **novos** e para uma fatia rotativa dos atos existentes, baixa
   a pagina de detalhe e calcula:
   - `status` (Vigente / Revogado) — lido das tags da pagina;
   - `content_hash` (SHA-256 do texto) — usado para detectar atualizacoes.
3. Compara com o estado anterior, salvo num SQLite local
   (`anatel_state.sqlite3`).
4. Envia o relatorio por email e salva uma copia HTML em `relatorios/`.

A **primeira execucao** apenas faz o snapshot inicial (nao manda email,
para nao inundar voce com centenas de "atos novos").

O script respeita o servidor: 2 segundos entre requisicoes, no maximo 60
paginas de detalhe por execucao, User-Agent identificado.

---

## 2. Setup rapido (rodando no seu computador)

```bash
# 1. Clone ou descompacte a pasta deste projeto, entre nela
cd monitor-anatel

# 2. Crie um virtualenv (opcional mas recomendado)
python3 -m venv .venv && source .venv/bin/activate    # Linux/Mac
# ou
python -m venv .venv && .venv\Scripts\activate         # Windows

# 3. Instale dependencias
pip install -r requirements.txt

# 4. Configure credenciais de email
cp .env.example .env
# Edite .env com suas informacoes SMTP

# 5. Carregue as variaveis e rode (Linux/Mac)
export $(grep -v '^#' .env | xargs)
python monitor_anatel.py
```

Na primeira execucao o script vai dizer "Banco vazio: esta sera a execucao
de inicializacao." e nao mandara email. Confirme que ele rodou ate o fim
sem erros, e ja estara pronto para o uso diario.

### Credenciais de email

**Gmail:** vai precisar criar uma "Senha de App" em
https://myaccount.google.com/apppasswords (exige 2FA ativado).
Coloque essa senha de 16 caracteres em `SMTP_PASSWORD`.

**Outlook/Office 365:** `smtp.office365.com` porta 587, sua senha normal
(ou senha de app se sua organizacao exigir).

**Servidor da empresa:** pergunte ao TI o host SMTP e a porta.

---

## 3. Como agendar para rodar diariamente

### Opcao A — GitHub Actions (recomendado, gratuito, sem servidor)

1. Crie um repositorio **privado** no GitHub e suba estes arquivos.
2. Em `Settings -> Secrets and variables -> Actions -> New repository secret`,
   cadastre os secrets: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`,
   `SMTP_PASSWORD`, `EMAIL_FROM`, `EMAIL_TO`.
3. O workflow em `.github/workflows/monitor.yml` ja esta configurado para
   rodar de segunda a sexta as 12:00 UTC (09:00 em Brasilia).
4. Para testar antes de esperar o agendamento: aba **Actions** ->
   "Monitor Anatel diario" -> **Run workflow**.

O workflow tambem commita automaticamente o `anatel_state.sqlite3`
atualizado de volta no repositorio, o que mantem o estado entre execucoes.

### Opcao B — Cron no Linux/Mac

Edite `crontab -e` e adicione:

```cron
0 9 * * 1-5 cd /caminho/para/monitor-anatel && /caminho/para/monitor-anatel/.venv/bin/python monitor_anatel.py >> monitor.log 2>&1
```

Roda todo dia util as 09h. Carregue o `.env` no inicio (use por exemplo
um script wrapper `run.sh` que faz `set -a; source .env; set +a` antes).

### Opcao C — Agendador de Tarefas do Windows

1. Abra "Agendador de Tarefas".
2. Criar tarefa -> Disparador "Diariamente, 09:00".
3. Acao: Iniciar programa `python.exe` com argumento `monitor_anatel.py`
   e diretorio inicial igual a pasta do projeto.
4. Variaveis de ambiente: configure via "Painel de Controle -> Sistema ->
   Variaveis de Ambiente" ou use um `.bat` wrapper que faz `set
   SMTP_USER=...` antes de chamar o python.

---

## 4. Quando o script pode falhar

- **Anatel muda o HTML do portal.** O parser pode parar de achar os links.
  Se isso acontecer, o log dira "0 ato(s) encontrado(s) na listagem". A
  funcao a ajustar e `parse_listagem()` em `monitor_anatel.py`.
- **Anatel bloqueia o IP.** Se voce rodar em paralelo em muitos lugares,
  pode tomar 403. Diminua a frequencia ou reduza categorias.
- **Email com falha de TLS.** Verifique a porta (587 para STARTTLS, 465
  para SSL puro — neste caso o codigo precisa de pequena adaptacao).

Logs ficam em stdout; com `>> monitor.log 2>&1` no cron voce os captura.

---

## 5. Limites conhecidos

- Detecta revogacao apenas quando a pagina de detalhe mostra a tag
  `AtoRevogado`. Se a Anatel apenas remover o ato sem essa tag, o script
  apenas registra um aviso "ato sumiu da listagem" no log.
- Nao consulta o DOU diretamente; baseia-se na publicacao no portal da
  Anatel, que normalmente ocorre poucas horas/dias depois.
- O hash de conteudo eh sensivel a pequenas mudancas de menu/cookies; se
  voce ver "atualizacoes" frequentes que nao parecem reais, ajuste o
  seletor em `parse_detalhe()` para isolar melhor o miolo da pagina.

---

## 6. Estendendo

- **Filtrar por palavras-chave** (ex.: "5G", "CPE", "homologacao"):
  no fim de `detectar_mudancas()`, filtre `novos`/`atualizados` antes de
  montar o relatorio.
- **Enviar para Slack/Telegram/Teams:** substitua `enviar_email()` por
  uma chamada `requests.post(webhook_url, json=...)`.
- **Monitorar tambem o DOU:** veja a Imprensa Nacional
  (https://www.in.gov.br) e o projeto "Querido Diario"
  (https://queridodiario.ok.org.br) — sao fontes complementares uteis.

---

Bom uso. Se o portal da Anatel mudar e o parsing quebrar, me mande o HTML
de uma pagina de listagem que ajusto.
