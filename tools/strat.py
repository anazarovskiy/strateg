#!/usr/bin/env python3
"""Точка входа CLI агента «Стратег». Запуск: ./strat <команда> или python3 tools/strat.py <команда>."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stratlib.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
