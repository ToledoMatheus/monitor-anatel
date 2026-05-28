#!/usr/bin/env python3
"""
resetar_status.py
-----------------
Script de uso UNICO: zera o campo last_detail_at de todos os atos do banco,
forcando o monitor_anatel.py a re-baixar a pagina de detalhe de todos eles
na proxima execucao.

Util quando:
- Mudamos o parser de status (ex: agora reconhece ResolucaoVigente)
- Queremos atualizar o content_hash de todo o banco
- O banco esta com muitos status "Desconhecido"

Apos rodar este script uma vez (via workflow manual), pode deletar do repo.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path("anatel_state.sqlite3")

def main():
    if not DB_PATH.exists():
        print(f"Banco {DB_PATH} nao encontrado.")
        return 1

    conn = sqlite3.connect(DB_PATH)

    # Conta antes
    antes = conn.execute("SELECT COUNT(*) FROM atos").fetchone()[0]
    print(f"Total de atos no banco: {antes}")

    # Reseta last_detail_at para forcar re-checagem
    conn.execute("UPDATE atos SET last_detail_at = NULL")
    conn.commit()

    afetados = conn.total_changes
    print(f"Zeramos last_detail_at de {afetados} ato(s).")
    print("Na proxima execucao do monitor_anatel.py, todos serao re-checados.")

    conn.close()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
