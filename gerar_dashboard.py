#!/usr/bin/env python3
"""
gerar_dashboard.py
------------------
Le o banco SQLite (anatel_state.sqlite3) e gera os arquivos estaticos
do dashboard publico:

  docs/dados.json   - todos os atos em formato JSON (para o HTML consumir)
  docs/stats.json   - estatisticas agregadas (totais por categoria, status etc.)

O dashboard HTML (docs/index.html) consome estes arquivos via fetch()
no navegador, sem precisar de servidor.

Executado pelo workflow do GitHub Actions logo apos o monitor_anatel.py.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuracao
# ---------------------------------------------------------------------------

DB_PATH = Path("anatel_state.sqlite3")
DOCS_DIR = Path("docs")
DADOS_JSON = DOCS_DIR / "dados.json"
STATS_JSON = DOCS_DIR / "stats.json"

# Mapeamento dos slugs para nomes amigaveis (mesmo dicionario do monitor)
CATEGORIES = {
    "atos-de-certificacao-de-produtos": "Atos de Certificacao de Produtos",
    "atos-de-requisitos-tecnicos-de-gestao-do-espectro": "Atos de Requisitos Tecnicos de Gestao do Espectro",
    "atos-de-numeracao": "Atos de Numeracao",
    "resolucoes": "Resolucoes",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("gerar-dashboard")


# ---------------------------------------------------------------------------
# Leitura do banco
# ---------------------------------------------------------------------------


def carregar_atos() -> list[dict]:
    """Le todos os atos do SQLite e retorna como lista de dicionarios."""
    if not DB_PATH.exists():
        log.warning("Banco %s nao encontrado. Gerando dashboard vazio.", DB_PATH)
        return []

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    atos = []
    for row in conn.execute(
        """SELECT url, category_slug, year, numero, titulo, status,
                  first_seen_at, last_seen_at
           FROM atos
           ORDER BY year DESC, CAST(numero AS INTEGER) DESC"""
    ):
        atos.append({
            "url": row["url"],
            "categoria_slug": row["category_slug"],
            "categoria_nome": CATEGORIES.get(row["category_slug"], row["category_slug"]),
            "ano": row["year"],
            "numero": row["numero"] or "",
            "titulo": row["titulo"] or "",
            "status": row["status"] or "Desconhecido",
            "primeira_vez_visto": row["first_seen_at"],
            "ultima_vez_visto": row["last_seen_at"],
        })

    conn.close()
    log.info("Carregados %d atos do banco", len(atos))
    return atos


def calcular_estatisticas(atos: list[dict]) -> dict:
    """Gera totais agregados para os cards no topo do dashboard."""
    total = len(atos)

    por_categoria = Counter(a["categoria_nome"] for a in atos)
    por_status = Counter(a["status"] for a in atos)
    por_ano = Counter(a["ano"] for a in atos)

    # Quantos atos novos nos ultimos 30 dias (baseado em first_seen_at)
    hoje = datetime.now(timezone.utc)
    novos_30d = 0
    for a in atos:
        if not a["primeira_vez_visto"]:
            continue
        try:
            dt = datetime.fromisoformat(a["primeira_vez_visto"].replace("Z", "+00:00"))
            if (hoje - dt).days <= 30:
                novos_30d += 1
        except (ValueError, AttributeError):
            pass

    return {
        "total": total,
        "vigentes": por_status.get("Vigente", 0),
        "revogados": por_status.get("Revogado", 0),
        "desconhecidos": por_status.get("Desconhecido", 0),
        "novos_ultimos_30_dias": novos_30d,
        "por_categoria": dict(por_categoria),
        "por_ano": dict(sorted(por_ano.items(), reverse=True)),
        "ultima_atualizacao": hoje.isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------------------
# Geracao dos arquivos
# ---------------------------------------------------------------------------


def gerar_arquivos():
    DOCS_DIR.mkdir(exist_ok=True)

    atos = carregar_atos()
    stats = calcular_estatisticas(atos)

    # Escreve dados.json (lista completa)
    with open(DADOS_JSON, "w", encoding="utf-8") as f:
        json.dump({"atos": atos}, f, ensure_ascii=False, indent=2)
    log.info("Gerado %s (%d atos)", DADOS_JSON, len(atos))

    # Escreve stats.json (agregados)
    with open(STATS_JSON, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    log.info("Gerado %s", STATS_JSON)


if __name__ == "__main__":
    gerar_arquivos()
