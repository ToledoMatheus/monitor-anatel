#!/usr/bin/env python3
"""
monitor_anatel.py
-----------------
Monitora diariamente as listas de Atos publicados pela Anatel e envia um
relatorio por email com:

  - Atos NOVOS  (apareceram desde a ultima execucao)
  - Atos ATUALIZADOS (conteudo da pagina mudou OU status mudou)
  - Atos REVOGADOS (tag "AtoRevogado" detectada na pagina de detalhe)

O estado e guardado num banco SQLite local. A primeira execucao apenas
faz o "snapshot inicial" e nao envia email (para evitar inundacao).

Categorias monitoradas por padrao (focadas em OCD):
  - Atos de Certificacao de Produtos
  - Atos de Requisitos Tecnicos de Gestao do Espectro
  - Atos de Numeracao

Configure via variaveis de ambiente (ver .env.example) ou edite CONFIG.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import smtplib
import sqlite3
import sys
import time
from dataclasses import dataclass
from collections import defaultdict
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Iterable

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Configuracao
# ---------------------------------------------------------------------------

# Categorias a monitorar. A chave eh o "slug" usado na URL da Anatel.
# Adicione/remova conforme sua necessidade. Os tres abaixo cobrem o escopo
# tipico de um OCD; para tambem acompanhar resolucoes, descomente.
CATEGORIES = {
    "atos-de-certificacao-de-produtos": "Atos de Certificacao de Produtos",
    "atos-de-requisitos-tecnicos-de-gestao-do-espectro": "Atos de Requisitos Tecnicos de Gestao do Espectro",
    "atos-de-numeracao": "Atos de Numeracao",
    "resolucoes": "Resolucoes",
    # "sumulas": "Sumulas",
    # "portarias-normativas": "Portarias Normativas",
}

BASE_URL = "https://informacoes.anatel.gov.br/legislacao"
USER_AGENT = (
    "Mozilla/5.0 (Monitor-Anatel/1.0; OCD compliance monitor; "
    "respeitando robots.txt e usando 1 req/2s)"
)
REQUEST_TIMEOUT = 30  # segundos
REQUEST_DELAY = 2.0   # segundos entre requisicoes (gentileza com o servidor)
MAX_DETAIL_FETCHES_PER_RUN = 60  # limite de paginas de detalhe por execucao

# Anos a varrer em cada execucao. Por padrao apenas o ano corrente.
# Em janeiro convem incluir o ano anterior tambem para nao perder publicacoes
# de fim de ano.
def years_to_check() -> list[int]:
    now = datetime.now()
    if now.month == 1:
        return [now.year, now.year - 1]
    return [now.year]


# Caminho do banco SQLite (estado entre execucoes)
DB_PATH = Path(os.environ.get("ANATEL_DB", "anatel_state.sqlite3"))

# Email (use variaveis de ambiente; ver .env.example)
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
EMAIL_FROM = os.environ.get("EMAIL_FROM", SMTP_USER)
EMAIL_TO = [e.strip() for e in os.environ.get("EMAIL_TO", "").split(",") if e.strip()]
EMAIL_SUBJECT_PREFIX = os.environ.get("EMAIL_SUBJECT_PREFIX", "[Monitor Anatel]")

# Comportamentos
SEND_EMAIL_IF_NO_CHANGES = os.environ.get("SEND_EMAIL_IF_NO_CHANGES", "0") == "1"
SAVE_REPORT_LOCALLY = True
REPORT_DIR = Path("relatorios")

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("monitor-anatel")


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------


@dataclass
class Ato:
    category_slug: str
    year: int
    url: str
    titulo: str
    numero: str | None = None
    status: str | None = None  # "Vigente" | "Revogado" | "Desconhecido"
    content_hash: str | None = None
    first_seen_at: str | None = None
    last_seen_at: str | None = None


@dataclass
class Diff:
    novos: list[Ato]
    atualizados: list[tuple[Ato, dict]]  # (ato, {campo: (antes, depois)})
    revogados: list[Ato]


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT})


def _fetch(url: str) -> str | None:
    """Faz GET com retry simples e respeita REQUEST_DELAY."""
    for tentativa in range(3):
        try:
            time.sleep(REQUEST_DELAY)
            resp = _session.get(url, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                # Anatel usa charset latin-1 em algumas paginas; deixa requests detectar
                resp.encoding = resp.apparent_encoding or resp.encoding
                return resp.text
            log.warning("GET %s -> HTTP %s (tentativa %d)", url, resp.status_code, tentativa + 1)
        except requests.RequestException as e:
            log.warning("GET %s falhou: %s (tentativa %d)", url, e, tentativa + 1)
        time.sleep(3 * (tentativa + 1))
    log.error("Desistindo de %s apos 3 tentativas", url)
    return None


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


_NUMERO_RE = re.compile(r"ato[\s\-_]*n?[oº]?\s*(\d+)", re.IGNORECASE)


def parse_listagem(html: str, category_slug: str, year: int) -> list[Ato]:
    """Extrai todos os links de ato da pagina de listagem do ano."""
    soup = BeautifulSoup(html, "html.parser")
    atos: dict[str, Ato] = {}

    # As paginas listam atos como <a href="..."> dentro da area de conteudo.
    # Estrategia robusta: pegar todos os links que apontam para a categoria/ano
    # e cujo texto contem "Ato".
    for a in soup.find_all("a", href=True):
        href = a["href"]
        texto = " ".join(a.get_text(" ", strip=True).split())
        if not href or not texto:
            continue
        # Normaliza URL
        if href.startswith("/"):
            href = "https://informacoes.anatel.gov.br" + href
        if not href.startswith("http"):
            continue
        # Filtra: precisa estar dentro da categoria e do ano
        if category_slug not in href:
            continue
        if f"/{year}/" not in href and not href.endswith(f"/{year}"):
            continue
        # Filtra apenas itens que parecem ser um documento individual (nao a propria lista).
        # URLs de detalhe seguem o padrao /{id}-{tipo}-{numero}, onde {tipo} pode ser:
        # - ato, ato-sor, resolucao, resolucao-interna, sumula, portaria etc.
        if not re.search(r"/\d+-(ato|resolucao|sumula|portaria)[\w\-]*-\d+(/|$)", href, re.IGNORECASE):
            continue

        # Deduplica pela URL
        if href in atos:
            continue

        numero = None
        m = _NUMERO_RE.search(texto)
        if m:
            numero = m.group(1)
        else:
            # Tenta extrair o numero da URL: pega o que vem depois do tipo (ato, resolucao, etc.)
            m = re.search(r"-(?:ato|resolucao|sumula|portaria)[\w\-]*-(\d+)", href, re.IGNORECASE)
            if m:
                numero = m.group(1)

        atos[href] = Ato(
            category_slug=category_slug,
            year=year,
            url=href,
            titulo=texto,
            numero=numero,
        )

    return list(atos.values())


def parse_detalhe(html: str) -> tuple[str, str]:
    """Retorna (status, content_hash) da pagina de detalhe de um ato.

    - status: "Vigente", "Revogado" ou "Desconhecido"
    - content_hash: sha256 do corpo principal, ignorando menu e rodape
    """
    soup = BeautifulSoup(html, "html.parser")

    # Tenta isolar o conteudo principal. O portal usa Joomla; o conteudo
    # normalmente fica dentro de um <div class="item-page"> ou similar.
    main = (
        soup.find("div", class_=re.compile(r"item-page|content|article|main", re.I))
        or soup.body
        or soup
    )
    texto = main.get_text("\n", strip=True)

    status = "Desconhecido"
    texto_lower = texto.lower()
    # A pagina marca o status como tag "AtoVigente" ou "AtoRevogado" no rodape de assuntos.
    if "atorevogado" in texto_lower.replace(" ", ""):
        status = "Revogado"
    elif "atovigente" in texto_lower.replace(" ", ""):
        status = "Vigente"
    # Fallback: procura "revogado" / "vigente" no texto
    elif re.search(r"\brevogado\b", texto_lower):
        # heuristica fraca; so use se nao achou tag
        status = "Revogado"
    elif re.search(r"\bvigente\b", texto_lower):
        status = "Vigente"

    content_hash = hashlib.sha256(texto.encode("utf-8", errors="ignore")).hexdigest()
    return status, content_hash


# ---------------------------------------------------------------------------
# Estado em SQLite
# ---------------------------------------------------------------------------


SCHEMA = """
CREATE TABLE IF NOT EXISTS atos (
    url             TEXT PRIMARY KEY,
    category_slug   TEXT NOT NULL,
    year            INTEGER NOT NULL,
    numero          TEXT,
    titulo          TEXT,
    status          TEXT,
    content_hash    TEXT,
    first_seen_at   TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL,
    last_detail_at  TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    novos           INTEGER DEFAULT 0,
    atualizados     INTEGER DEFAULT 0,
    revogados       INTEGER DEFAULT 0,
    erros           INTEGER DEFAULT 0
);
"""


def open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def is_primeira_execucao(conn: sqlite3.Connection) -> bool:
    cur = conn.execute("SELECT COUNT(*) AS n FROM atos")
    return cur.fetchone()["n"] == 0


def load_atos_existentes(conn: sqlite3.Connection) -> dict[str, Ato]:
    out: dict[str, Ato] = {}
    for row in conn.execute("SELECT * FROM atos"):
        out[row["url"]] = Ato(
            category_slug=row["category_slug"],
            year=row["year"],
            url=row["url"],
            numero=row["numero"],
            titulo=row["titulo"] or "",
            status=row["status"],
            content_hash=row["content_hash"],
            first_seen_at=row["first_seen_at"],
            last_seen_at=row["last_seen_at"],
        )
    return out


def upsert_ato(conn: sqlite3.Connection, ato: Ato, *, agora: str, atualizou_detalhe: bool):
    existente = conn.execute("SELECT 1 FROM atos WHERE url = ?", (ato.url,)).fetchone()
    if existente is None:
        conn.execute(
            """INSERT INTO atos
               (url, category_slug, year, numero, titulo, status, content_hash,
                first_seen_at, last_seen_at, last_detail_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ato.url,
                ato.category_slug,
                ato.year,
                ato.numero,
                ato.titulo,
                ato.status,
                ato.content_hash,
                agora,
                agora,
                agora if atualizou_detalhe else None,
            ),
        )
    else:
        if atualizou_detalhe:
            conn.execute(
                """UPDATE atos
                   SET titulo = COALESCE(?, titulo),
                       numero = COALESCE(?, numero),
                       status = ?,
                       content_hash = ?,
                       last_seen_at = ?,
                       last_detail_at = ?
                   WHERE url = ?""",
                (
                    ato.titulo,
                    ato.numero,
                    ato.status,
                    ato.content_hash,
                    agora,
                    agora,
                    ato.url,
                ),
            )
        else:
            conn.execute(
                """UPDATE atos
                   SET titulo = COALESCE(?, titulo),
                       numero = COALESCE(?, numero),
                       last_seen_at = ?
                   WHERE url = ?""",
                (ato.titulo, ato.numero, agora, ato.url),
            )


# ---------------------------------------------------------------------------
# Logica principal
# ---------------------------------------------------------------------------


def coletar_listagens() -> list[Ato]:
    encontrados: list[Ato] = []
    for slug, nome in CATEGORIES.items():
        for ano in years_to_check():
            url = f"{BASE_URL}/{slug}/{ano}"
            log.info("Coletando listagem: %s (%d)", nome, ano)
            html = _fetch(url)
            if not html:
                continue
            achados = parse_listagem(html, slug, ano)
            log.info("  %d ato(s) encontrado(s) na listagem", len(achados))
            encontrados.extend(achados)
    return encontrados


def detectar_mudancas(
    conn: sqlite3.Connection,
    atos_atuais: list[Ato],
    *,
    primeira_execucao: bool,
) -> Diff:
    """
    Compara listagem atual com o banco. Para itens novos e para itens
    que sao re-checados, baixa a pagina de detalhe para identificar
    status (Vigente/Revogado) e hash de conteudo (atualizacoes).
    """
    agora = datetime.now(timezone.utc).isoformat(timespec="seconds")
    existentes = load_atos_existentes(conn)

    novos: list[Ato] = []
    atualizados: list[tuple[Ato, dict]] = []
    revogados: list[Ato] = []

    # Decide quais paginas de detalhe baixar nesta execucao
    urls_para_detalhar: list[str] = []
    for ato in atos_atuais:
        if ato.url not in existentes:
            urls_para_detalhar.append(ato.url)

    # Adiciona uma fatia rotativa de atos antigos para detectar revogacao tardia.
    # Pegamos os com last_detail_at mais antigo (ou nulo).
    espaco = max(0, MAX_DETAIL_FETCHES_PER_RUN - len(urls_para_detalhar))
    if espaco > 0:
        rows = conn.execute(
            """SELECT url FROM atos
               WHERE status != 'Revogado' OR status IS NULL
               ORDER BY COALESCE(last_detail_at, '0') ASC
               LIMIT ?""",
            (espaco,),
        ).fetchall()
        urls_para_detalhar.extend(r["url"] for r in rows)

    urls_para_detalhar = list(dict.fromkeys(urls_para_detalhar))[:MAX_DETAIL_FETCHES_PER_RUN]
    log.info("Vou baixar %d pagina(s) de detalhe nesta execucao", len(urls_para_detalhar))

    # Indexa lista atual por url
    atuais_por_url = {a.url: a for a in atos_atuais}

    # Processa primeiro todos os que estao na listagem
    for ato in atos_atuais:
        precisa_detalhar = ato.url in urls_para_detalhar
        diffs: dict = {}

        anterior = existentes.get(ato.url)

        if precisa_detalhar:
            html = _fetch(ato.url)
            if html:
                ato.status, ato.content_hash = parse_detalhe(html)

        if anterior is None:
            # NOVO
            novos.append(ato)
            upsert_ato(conn, ato, agora=agora, atualizou_detalhe=precisa_detalhar)
        else:
            # Comparacoes
            if precisa_detalhar:
                if anterior.status != ato.status and ato.status:
                    diffs["status"] = (anterior.status, ato.status)
                if ato.content_hash and anterior.content_hash and anterior.content_hash != ato.content_hash:
                    diffs["conteudo"] = ("hash diferente", ato.content_hash[:12])
            if ato.titulo and ato.titulo != (anterior.titulo or ""):
                diffs["titulo"] = (anterior.titulo or "", ato.titulo)

            if diffs:
                if diffs.get("status") and diffs["status"][1] == "Revogado":
                    revogados.append(ato)
                else:
                    atualizados.append((ato, diffs))

            upsert_ato(conn, ato, agora=agora, atualizou_detalhe=precisa_detalhar)

    # Itens que SUMIRAM da listagem nao sao necessariamente revogados,
    # mas vale registrar no log para investigacao manual.
    urls_atuais = set(atuais_por_url.keys())
    sumidos = [
        existentes[u]
        for u in existentes
        if u not in urls_atuais
        and existentes[u].year in years_to_check()
    ]
    if sumidos:
        log.warning(
            "%d ato(s) que existiam no banco nao apareceram na listagem desta execucao",
            len(sumidos),
        )
        for s in sumidos[:5]:
            log.warning("  - %s (%s)", s.titulo, s.url)

    conn.commit()

    # Na primeira execucao, NAO reportamos novos (e snapshot inicial)
    if primeira_execucao:
        log.info("Primeira execucao: snapshot inicial salvo, nada sera enviado por email.")
        return Diff(novos=[], atualizados=[], revogados=[])

    return Diff(novos=novos, atualizados=atualizados, revogados=revogados)


# ---------------------------------------------------------------------------
# Relatorio
# ---------------------------------------------------------------------------


def montar_relatorio_html(diff: Diff) -> str:
    """
    Monta o relatorio HTML agrupando os itens primeiro por tipo de evento
    (novos / atualizados / revogados) e depois por categoria dentro de
    cada bloco.
    """

    def li_ato(a: Ato, extra: str = "") -> str:
        # Cada item agora nao precisa mais mostrar a categoria entre colchetes,
        # porque ja estamos dentro do sub-bloco da categoria.
        return (
            f'<li><a href="{a.url}">{(a.titulo or a.url)}</a>'
            f'{(" <em>" + extra + "</em>") if extra else ""}</li>'
        )

    def agrupar_por_categoria(itens: list) -> dict[str, list]:
        """Agrupa uma lista de atos (ou tuplas) por category_slug."""
        grupos: dict[str, list] = defaultdict(list)
        for item in itens:
            # Atualizados sao tuplas (Ato, diffs); novos e revogados sao Ato puro
            ato = item[0] if isinstance(item, tuple) else item
            grupos[ato.category_slug].append(item)
        return grupos

    def renderiza_bloco(titulo: str, cor: str, itens: list, tem_diffs: bool = False) -> str:
        """Renderiza um bloco (Novos/Atualizados/Revogados) agrupado por categoria."""
        if not itens:
            return ""
        partes = [f"<h3 style='color:{cor}'>{titulo} ({len(itens)})</h3>"]
        grupos = agrupar_por_categoria(itens)
        # Itera nas categorias na ordem em que aparecem em CATEGORIES (mantem consistencia visual)
        for slug in CATEGORIES:
            if slug not in grupos:
                continue
            nome_cat = CATEGORIES[slug]
            partes.append(f"<h4 style='margin:8px 0 4px 0;color:#333'>{nome_cat}</h4><ul>")
            for item in grupos[slug]:
                if tem_diffs:
                    ato, diffs = item
                    extras = "; ".join(f"{k}: {v[0]!r} -> {v[1]!r}" for k, v in diffs.items())
                    partes.append(li_ato(ato, extras))
                else:
                    partes.append(li_ato(item))
            partes.append("</ul>")
        return "".join(partes)

    partes = ["<html><body style='font-family:Arial,sans-serif'>"]
    partes.append(f"<h2>Monitor Anatel - {datetime.now().strftime('%d/%m/%Y %H:%M')}</h2>")
    partes.append(
        f"<p><b>Resumo:</b> {len(diff.novos)} novo(s), "
        f"{len(diff.atualizados)} atualizado(s), "
        f"{len(diff.revogados)} revogado(s).</p>"
    )

    partes.append(renderiza_bloco("Novos", "#0a7", diff.novos))
    partes.append(renderiza_bloco("Atualizados", "#a60", diff.atualizados, tem_diffs=True))
    partes.append(renderiza_bloco("Revogados", "#c00", diff.revogados))

    if not (diff.novos or diff.atualizados or diff.revogados):
        partes.append("<p><i>Nenhuma mudanca detectada hoje.</i></p>")

    partes.append(
        "<hr><p style='color:#888;font-size:12px'>Gerado automaticamente por monitor_anatel.py. "
        "Fonte: portal de Legislacao da Anatel.</p></body></html>"
    )
    return "".join(partes)
  
def enviar_email(html: str, diff: Diff) -> bool:
    if not (SMTP_HOST and SMTP_USER and SMTP_PASSWORD and EMAIL_TO):
        log.warning("SMTP nao configurado; pulando envio de email.")
        return False

    if not SEND_EMAIL_IF_NO_CHANGES and not (diff.novos or diff.atualizados or diff.revogados):
        log.info("Sem mudancas e SEND_EMAIL_IF_NO_CHANGES=0; nao envio email.")
        return False

    msg = MIMEMultipart("alternative")
    resumo = f"{len(diff.novos)}N / {len(diff.atualizados)}A / {len(diff.revogados)}R"
    msg["Subject"] = f"{EMAIL_SUBJECT_PREFIX} {datetime.now():%d/%m/%Y} - {resumo}"
    msg["From"] = EMAIL_FROM
    msg["To"] = ", ".join(EMAIL_TO)

    texto_simples = re.sub(r"<[^>]+>", "", html)
    msg.attach(MIMEText(texto_simples, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as srv:
            srv.starttls()
            srv.login(SMTP_USER, SMTP_PASSWORD)
            srv.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
        log.info("Email enviado para %s", ", ".join(EMAIL_TO))
        return True
    except Exception as e:
        log.exception("Falha ao enviar email: %s", e)
        return False


def salvar_relatorio_local(html: str) -> Path:
    REPORT_DIR.mkdir(exist_ok=True)
    nome = REPORT_DIR / f"relatorio-{datetime.now():%Y-%m-%d}.html"
    nome.write_text(html, encoding="utf-8")
    log.info("Relatorio salvo em %s", nome)
    return nome


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    log.info("=== Inicio da execucao ===")
    conn = open_db()
    primeira = is_primeira_execucao(conn)
    if primeira:
        log.info("Banco vazio: esta sera a execucao de inicializacao.")

    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute("INSERT INTO runs (started_at) VALUES (?)", (started,))
    run_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.commit()

    try:
        atos = coletar_listagens()
        diff = detectar_mudancas(conn, atos, primeira_execucao=primeira)

        html = montar_relatorio_html(diff)
        if SAVE_REPORT_LOCALLY:
            salvar_relatorio_local(html)
        enviar_email(html, diff)

        conn.execute(
            """UPDATE runs SET finished_at=?, novos=?, atualizados=?, revogados=?
               WHERE id=?""",
            (
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                len(diff.novos),
                len(diff.atualizados),
                len(diff.revogados),
                run_id,
            ),
        )
        conn.commit()
        log.info(
            "=== Fim: %d novos, %d atualizados, %d revogados ===",
            len(diff.novos),
            len(diff.atualizados),
            len(diff.revogados),
        )
        return 0
    except Exception:
        log.exception("Erro fatal na execucao")
        conn.execute(
            "UPDATE runs SET finished_at=?, erros=1 WHERE id=?",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), run_id),
        )
        conn.commit()
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
