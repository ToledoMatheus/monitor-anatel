# Monitor Anatel para OCD

Sistema automatizado que verifica diariamente as listas de **Atos** e
**Resolucoes** publicados pela Anatel no portal de Legislacao, envia
relatorios por email e disponibiliza um **dashboard publico** com filtros,
graficos e exportacao em CSV.

Construido para uso em Organismos de Certificacao Designados (OCDs), mas
util para qualquer profissional que precise acompanhar a regulamentacao da
Anatel sem entrar manualmente no portal todo dia.

🌐 **Dashboard publico:** https://toledomatheus.github.io/monitor-anatel/

---

## 1. Visao geral

O sistema detecta e notifica:

- **Atos / Resolucoes novos** — apareceram desde a ultima execucao
- **Atos / Resolucoes atualizados** — conteudo da pagina mudou ou titulo mudou
- **Atos / Resolucoes revogados** — pagina passou a exibir tag `AtoRevogado` ou `ResolucaoRevogada`

### Categorias monitoradas

| Categoria | Slug usado nas URLs da Anatel |
|---|---|
| Atos de Certificacao de Produtos | `atos-de-certificacao-de-produtos` |
| Atos de Requisitos Tecnicos de Gestao do Espectro | `atos-de-requisitos-tecnicos-de-gestao-do-espectro` |
| Atos de Numeracao | `atos-de-numeracao` |
| Resolucoes | `resolucoes` |

Para adicionar Sumulas, Portarias Normativas etc., edite o dicionario
`CATEGORIES` no topo de `monitor_anatel.py`.

### Componentes do sistema

| Arquivo | Funcao |
|---|---|
| `monitor_anatel.py` | Coleta atos do portal, compara com estado anterior, envia email |
| `gerar_dashboard.py` | Le o SQLite e gera `dados.json` + `stats.json` para o dashboard |
| `docs/index.html` | Dashboard web (HTML + CSS + JS + Chart.js) |
| `anatel_state.sqlite3` | Banco de dados com a memoria das execucoes anteriores |
| `.github/workflows/monitor.yml` | Agendamento automatico no GitHub Actions |
| `relatorios/` | Copias HTML dos relatorios diarios |
| `docs/` | Arquivos do dashboard publico (servidos via GitHub Pages) |

---

## 2. Como funciona

1. **Em cada execucao**, baixa a lista de atos do **ano corrente** (em
   janeiro tambem o ano anterior) para cada categoria.
2. Para atos **novos** e para uma fatia rotativa dos atos existentes,
   baixa a pagina de detalhe e extrai:
   - `status` (Vigente / Revogado / Desconhecido) — lido das tags da
     pagina, com normalizacao de acentos para suportar tanto `AtoVigente`
     quanto `ResolucaoVigente`;
   - `content_hash` (SHA-256 do texto principal) — usado para detectar
     atualizacoes futuras.
3. Compara com o estado anterior, salvo num SQLite local
   (`anatel_state.sqlite3`).
4. Envia um relatorio HTML por email **agrupado por tipo de evento e por
   categoria** (Novos / Atualizados / Revogados → cada um com sub-blocos
   por categoria).
5. Salva uma copia HTML em `relatorios/relatorio-YYYY-MM-DD.html`.
6. Roda `gerar_dashboard.py` para atualizar os JSONs do dashboard.
7. Commita o estado atualizado de volta no repositorio (mantem memoria
   entre execucoes).

A **primeira execucao** apenas faz o snapshot inicial (nao manda email,
para nao inundar voce com dezenas de "atos novos" historicos).

O script respeita o servidor da Anatel: **2 segundos entre requisicoes**,
no maximo **60 paginas de detalhe por execucao**, User-Agent identificado.

---

## 3. Setup rapido (rodando no seu computador)

```bash
# 1. Clone o repositorio
git clone https://github.com/ToledoMatheus/monitor-anatel.git
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

# 6. (Opcional) gere os arquivos do dashboard
python gerar_dashboard.py
```

Na primeira execucao o script vai dizer "Banco vazio: esta sera a execucao
de inicializacao." e nao mandara email.

### Credenciais de email

**Gmail:** crie uma "Senha de App" em
https://myaccount.google.com/apppasswords (exige 2FA ativado).
Coloque essa senha de 16 caracteres em `SMTP_PASSWORD`.

**Outlook/Office 365:** `smtp.office365.com` porta 587, sua senha normal
(ou senha de app se sua organizacao exigir).

**Servidor da empresa:** pergunte ao TI o host SMTP e a porta.

---

## 4. Agendamento automatico via GitHub Actions

### 4.1. Setup inicial

1. Faca fork do repositorio (ou suba os arquivos para um repo proprio).
2. Em **Settings → Secrets and variables → Actions**, cadastre os secrets:

   | Secret | Exemplo |
   |---|---|
   | `SMTP_HOST` | `smtp.gmail.com` |
   | `SMTP_PORT` | `587` |
   | `SMTP_USER` | `voce@gmail.com` |
   | `SMTP_PASSWORD` | senha de app de 16 caracteres |
   | `EMAIL_FROM` | `voce@gmail.com` |
   | `EMAIL_TO` | `voce@gmail.com,colega@empresa.com` (separados por virgula) |

3. Em **Settings → Actions → General → Workflow permissions**, marque
   **"Read and write permissions"** (necessario para o bot commitar o
   estado do banco).

4. O workflow em `.github/workflows/monitor.yml` ja esta configurado para
   rodar de segunda a sexta as **12:00 UTC (09:00 em Brasilia)**.

5. Para testar antes de esperar o agendamento: aba **Actions → "Monitor
   Anatel diario" → Run workflow**.

### 4.2. Variaveis opcionais (em Variables, nao Secrets)

| Variavel | Valor | Efeito |
|---|---|---|
| `SEND_EMAIL_IF_NO_CHANGES` | `1` | Envia email todo dia mesmo sem mudancas (heartbeat). Util para confirmar que o sistema esta vivo. |

Apague a variavel para voltar ao modo "email so quando houver novidade".

### 4.3. Cron alternativo (Linux/Mac)

```cron
0 9 * * 1-5 cd /caminho/para/monitor-anatel && /caminho/para/monitor-anatel/.venv/bin/python monitor_anatel.py >> monitor.log 2>&1
```

---

## 5. Dashboard publico (GitHub Pages)

O dashboard fica acessivel via URL publica e mostra em tempo real (com
atualizacao diaria) todos os atos monitorados.

🌐 **URL:** `https://<seu-usuario>.github.io/monitor-anatel/`

### Recursos do dashboard

- 5 **cards de estatistica** no topo: Total monitorado, Vigentes,
  Revogados, Novos (30 dias), Categorias
- **Grafico de pizza** com distribuicao por categoria (Chart.js)
- **Filtros** por categoria, status, ano e busca textual
- **Tabela paginada** com link clicavel para a pagina da Anatel de cada ato
- **Botao "Baixar CSV"** que exporta o filtro atual no padrao brasileiro
  (separador `;`, BOM UTF-8 para abrir corretamente no Excel-BR)
- **Atualizacao automatica** depois de cada execucao do workflow

### Ativacao do GitHub Pages

1. **Settings → Pages**
2. Source: **Deploy from a branch**
3. Branch: `main` | Folder: `/docs`
4. Save.

Em 1-5 minutos o site fica no ar.

⚠️ **GitHub Pages exige repositorio publico** no plano gratuito. Os dados
mostrados no dashboard sao publicos por natureza (vem do portal da
Anatel), entao isso nao expoe informacao sensivel. Os secrets continuam
protegidos no cofre criptografado do GitHub.

### Cache busting no carregamento dos JSONs

Para evitar que o navegador ou o GitHub Pages CDN sirvam versoes antigas
dos arquivos JSON apos uma atualizacao, o `docs/index.html` carrega os
arquivos com um query string baseado em timestamp:

```javascript
const versao = Date.now();
const [stats, dados] = await Promise.all([
  fetch(`stats.json?v=${versao}`, { cache: 'no-store' }).then(r => r.json()),
  fetch(`dados.json?v=${versao}`, { cache: 'no-store' }).then(r => r.json())
]);
```

Isso forca o navegador a sempre buscar a versao mais recente.

---

## 6. Detalhes tecnicos importantes

### 6.1. Normalizacao de acentos no parser

A Anatel marca o status dos documentos com tags como `AtoVigente`,
`AtoRevogado`, `ResolucaoVigente`, `ResolucaoRevogada`. O problema e que
no HTML real, **as tags de Resolucao vem com acentos**: `ResoluçãoVigente`
e `ResoluçãoRevogada`.

Para tratar isso de forma robusta, o `monitor_anatel.py` usa esta funcao
auxiliar (que remove acentos via Unicode NFKD e mantem so letras/numeros):

```python
import unicodedata

def normalizar_texto(texto: str) -> str:
    if not texto:
        return ""
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = texto.lower()
    texto = re.sub(r"[^a-z0-9]", "", texto)
    return texto
```

Assim, `ResoluçãoVigente` vira `resolucaovigente`, que casa com o regex
`(ato|resolucao|sumula|portaria)\w*vigente` independentemente de acentos,
espacos ou capitalizacao.

### 6.2. Onde procurar as tags

As tags de status aparecem **no rodape da pagina, na secao "Assunto(s)"**.
Para captura-las, o parser usa `soup.get_text(...)` na pagina inteira (e
nao apenas no `<div class="item-page">`), pois as tags ficam fora do
miolo principal do conteudo.

### 6.3. Filtro generalizado de URLs

O `parse_listagem()` reconhece URLs no padrao
`/{id}-{tipo}-{numero}` onde `{tipo}` pode ser `ato`, `resolucao`,
`sumula` ou `portaria`. Isso permite adicionar novas categorias apenas
descomentando linhas no dicionario `CATEGORIES`, sem alterar o parser.

---

## 7. Quando o sistema pode falhar

| Sintoma no log | Causa provavel | Como resolver |
|---|---|---|
| `0 ato(s) encontrado(s)` em todas as categorias | Anatel mudou o HTML do portal | Ajustar `parse_listagem()` |
| `0 ato(s) encontrado(s)` em uma categoria especifica | URL ou padrao mudou para aquela categoria | Verificar manualmente no navegador |
| Muitos status "Desconhecido" no dashboard | Anatel introduziu nova tag de status | Adicionar prefixo ao regex em `parse_detalhe()` |
| HTTP 403 nas requisicoes | Anatel bloqueou o IP do GitHub Actions | Diminuir frequencia ou aumentar `REQUEST_DELAY` |
| `Authentication failed` no SMTP | Senha de app expirada ou 2FA desabilitado | Gerar nova senha em myaccount.google.com/apppasswords |
| Workflow falha em "Commitar estado" | Sem permissao de escrita | Settings → Actions → "Read and write permissions" |

Logs ficam em **Actions → Monitor Anatel diario → ultimo run**.

---

## 8. Limites conhecidos

- Detecta revogacao apenas quando a pagina de detalhe mostra a tag
  correspondente. Se a Anatel apenas remover o ato sem essa tag, o
  script registra um aviso "ato sumiu da listagem" no log.
- Nao consulta o DOU diretamente; baseia-se na publicacao no portal da
  Anatel, que normalmente ocorre poucas horas/dias depois.
- Monitora apenas o **ano corrente** (em janeiro tambem o anterior).
  Mudancas em atos de anos anteriores nao sao detectadas.
- Maximo de 60 paginas de detalhe por execucao para nao sobrecarregar o
  servidor. Se houver muitos atos novos no mesmo dia, alguns ficam para
  o proximo run.

---

## 9. Manutencao em caso de mudanca de status historica

Quando o parser e atualizado para reconhecer novas tags (ex: adicao de
Resolucoes), os atos ja existentes no banco continuam com o status
antigo ate serem re-checados. Para forcar a re-checagem de todos,
execute o seguinte SQL no banco:

```sql
UPDATE atos SET last_detail_at = NULL;
```

Na proxima execucao do `monitor_anatel.py`, todos os atos serao
re-baixados (respeitando o limite de 60 por execucao). Se houver mais
de 60 atos, rode o workflow algumas vezes seguidas.

---

## 10. Estendendo o sistema

### Adicionar mais categorias

Edite `CATEGORIES` em `monitor_anatel.py`:

```python
CATEGORIES = {
    "atos-de-certificacao-de-produtos": "Atos de Certificacao de Produtos",
    "atos-de-requisitos-tecnicos-de-gestao-do-espectro": "Atos de Requisitos Tecnicos de Gestao do Espectro",
    "atos-de-numeracao": "Atos de Numeracao",
    "resolucoes": "Resolucoes",
    "sumulas": "Sumulas",
    "portarias-normativas": "Portarias Normativas",
}
```

### Filtrar por palavras-chave de interesse

No fim de `detectar_mudancas()`, filtre `novos`/`atualizados` antes de
montar o relatorio.

### Enviar para Slack / Telegram / Teams

Substitua ou complemente `enviar_email()` por uma chamada
`requests.post(webhook_url, json=...)`.

### Monitorar tambem o DOU

Veja a Imprensa Nacional (https://www.in.gov.br) e o projeto "Querido
Diario" (https://queridodiario.ok.org.br) — sao fontes complementares
uteis para publicacoes oficiais antes mesmo do portal da Anatel indexar.

### Analise por IA

Integre com a API do Claude ou OpenAI para gerar resumos automaticos de
cada ato novo (3 frases por ato), classificar o tipo de mudanca e
sinalizar impacto potencial para sua OCD.

---

## 11. Stack tecnico

| Camada | Tecnologia |
|---|---|
| Linguagem principal | Python 3.11 |
| Web scraping | requests + BeautifulSoup 4 |
| Banco de dados | SQLite (arquivo unico) |
| Email | smtplib (Gmail SMTP) |
| Hash de conteudo | hashlib (SHA-256) |
| Normalizacao Unicode | unicodedata (NFKD) |
| Automacao | GitHub Actions (cron + workflow_dispatch) |
| Dashboard | HTML + CSS + JavaScript (vanilla) |
| Graficos | Chart.js 4.4 (via CDN) |
| Hospedagem do dashboard | GitHub Pages (gratuito) |

Tudo gratuito, sem servidor proprio, sem manutencao continua.

---

## 12. Estrutura do repositorio

```
monitor-anatel/
├── .github/
│   └── workflows/
│       └── monitor.yml          # Agendamento + execucao automatica
├── docs/                        # Dashboard publico (servido pelo Pages)
│   ├── index.html               # Interface do dashboard
│   ├── dados.json               # Lista completa de atos (gerada)
│   └── stats.json               # Estatisticas agregadas (gerada)
├── relatorios/                  # Copias HTML dos relatorios diarios
│   └── relatorio-YYYY-MM-DD.html
├── monitor_anatel.py            # Script principal de monitoramento
├── gerar_dashboard.py           # Gera os JSONs do dashboard
├── anatel_state.sqlite3         # Banco de dados (memoria entre execucoes)
├── requirements.txt             # Dependencias Python
├── .env.example                 # Modelo de configuracao (sem credenciais)
├── .gitignore                   # Arquivos ignorados pelo Git
└── README.md                    # Este arquivo
```

---

## 13. Historico de versoes

- **v1.0** — Versao inicial. Monitoramento de 3 categorias de Atos com
  envio por email.
- **v1.1** — Suporte a multiplos destinatarios de email via virgula.
- **v1.2** — Adicao da categoria Resolucoes; regex de URL generalizado
  para `ato|resolucao|sumula|portaria`.
- **v1.3** — Relatorio agrupado por tipo de evento E por categoria
  (sub-blocos).
- **v1.4** — Parser de status com normalizacao Unicode para suportar
  tags com acentos (`ResoluçãoVigente`); leitura do `get_text()` da
  pagina inteira para encontrar tags no rodape.
- **v1.5** — Dashboard web publico via GitHub Pages com filtros,
  graficos e exportacao CSV no padrao brasileiro.

---

## 14. Contribuindo

Se o portal da Anatel mudar e o parsing quebrar, abra uma issue
incluindo:
- Print da pagina afetada
- HTML salvo da pagina (se possivel)
- Log do GitHub Actions com o erro

Pull requests bem-vindos.

---

## Licenca

Uso pessoal e profissional permitido. Os dados monitorados sao publicos
e pertencem a Anatel. Este projeto apenas facilita o acompanhamento.

## Autor
   Matheus Toledo — [LinkedIn](https://www.linkedin.com/in/matheus-rubim-de-toledo-a32aa0285/)
   Estudante de engenharia de Telecomunicações.
   Projeto criado em 2026 como ferramenta interna de acompanhamento regulatório.
