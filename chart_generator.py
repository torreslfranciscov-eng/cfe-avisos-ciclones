"""
chart_generator.py
Generador de tableros ejecutivos, tablas estilizadas e hidrogramas 72h en PNG
con identidad institucional CFE (verde #1E5B4F, dorado #B38E5D)
alimentados en tiempo real desde CFE Mirror API.
"""
from __future__ import annotations

import datetime
import io
import os
import unicodedata
import requests

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import FancyBboxPatch

CFE_GREEN = "#1E5B4F"
CFE_GREEN_DARK = "#123730"
CFE_GOLD = "#B38E5D"
BG_LIGHT = "#F8FAFC"
CARD_BG = "#FFFFFF"
BORDER_COLOR = "#CBD5E1"
TEXT_DARK = "#0F172A"
TEXT_MUTED = "#475569"
SEM_GREEN = "#16A34A"
SEM_YELLOW = "#D97706"
SEM_RED = "#DC2626"
BLUE_MAIN = "#2563EB"
BLUE_DARK = "#1E3A8A"

MIRROR_URL = os.getenv("CFE_MIRROR_API_URL", "https://cfe-mirror-api.onrender.com").rstrip("/")
MIRROR_KEY = os.getenv("CFE_MIRROR_API_KEY", "cfe_live_rWCDtjwhU-nEbp-jlMq0uMV4-AiwrU9lp1jWRw").strip()

plt.rcParams["font.family"] = "DejaVu Sans"
plt.rcParams["axes.edgecolor"] = BORDER_COLOR
plt.rcParams["axes.linewidth"] = 0.8


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", (s or "").lower())
        if unicodedata.category(c) != "Mn"
    )


def _fetch_mirror(endpoint: str, params: dict | None = None, timeout: int = 25) -> dict:
    clean = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
    r = requests.get(
        f"{MIRROR_URL}{endpoint}",
        headers={"X-API-Key": MIRROR_KEY, "Accept": "application/json", "User-Agent": "Centinela-Charts/1.0"},
        params=clean,
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()


def _fig_to_png_bytes(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def _draw_header(fig, title: str, subtitle: str):
    ax_h = fig.add_axes([0.0, 0.915, 1.0, 0.085])
    ax_h.set_xlim(0, 1)
    ax_h.set_ylim(0, 1)
    ax_h.axis("off")
    ax_h.add_patch(FancyBboxPatch((0, 0), 1, 1, boxstyle="square,pad=0", facecolor=CFE_GREEN, edgecolor="none"))
    ax_h.add_patch(FancyBboxPatch((0, 0), 1, 0.09, boxstyle="square,pad=0", facecolor=CFE_GOLD, edgecolor="none"))
    ax_h.text(0.025, 0.62, title, color="white", fontsize=15, fontweight="bold", va="center")
    ax_h.text(0.025, 0.25, subtitle, color="#E2E8F0", fontsize=9.5, va="center")
    ax_h.text(0.975, 0.50, "CFE · HIDROMETRÍA", color="#FDE68A", fontsize=10, fontweight="bold", va="center", ha="right")


def _draw_kpi_row(fig, kpis: list[tuple[str, str, str]], y_bottom=0.815, height=0.082):
    """Dibuja una fila de tarjetas KPI: [(titulo, valor, color_borde), ...]."""
    n = len(kpis)
    gap = 0.015
    margin = 0.025
    w = (1.0 - 2 * margin - gap * (n - 1)) / n
    for i, (label, val, accent) in enumerate(kpis):
        x = margin + i * (w + gap)
        ax = fig.add_axes([x, y_bottom, w, height])
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis("off")
        ax.add_patch(FancyBboxPatch((0.01, 0.04), 0.98, 0.92, boxstyle="round,pad=0.02,rounding_size=0.08",
                                    facecolor=CARD_BG, edgecolor=BORDER_COLOR, linewidth=1.0))
        ax.add_patch(FancyBboxPatch((0.01, 0.04), 0.035, 0.92, boxstyle="square,pad=0",
                                    facecolor=accent, edgecolor="none"))
        ax.text(0.09, 0.72, label, color=TEXT_MUTED, fontsize=8.5, fontweight="bold", va="center")
        ax.text(0.09, 0.30, val, color=TEXT_DARK, fontsize=13, fontweight="bold", va="center")


def _sem_color(semaforo: str) -> str:
    if semaforo == "ROJO":
        return SEM_RED
    if semaforo == "AMARILLO":
        return SEM_YELLOW
    return SEM_GREEN


# ==================== 1. TABLERO DE EMBALSES (OPCIÓN 13) ====================
def render_embalses_dashboard(crp: str | None = None, delta_hours: int = 3) -> bytes:
    params = {"only_dams": "true", "delta_hours": delta_hours, "include_text": "false"}
    if crp and crp.upper() != "ALL":
        params["crp"] = crp.upper()
    data = _fetch_mirror("/api/v1/water-levels", params)

    items = data.get("data") or []
    fecha = data.get("fecha_local", "")
    hora = data.get("hora_local", "")
    scope_lbl = crp.upper() if (crp and crp.upper() != "ALL") else "RED NACIONAL (5 CRP)"

    dams_vol = [x for x in items if x.get("almacenamiento_util_mm3") is not None and x.get("cap_util_namo_mm3")]
    tot_util = sum(x["almacenamiento_util_mm3"] for x in dams_vol)
    tot_cap = sum(x["cap_util_namo_mm3"] for x in dams_vol)
    tot_libre = max(0.0, tot_cap - tot_util)
    tot_pct = (tot_util / tot_cap * 100.0) if tot_cap > 0 else 0.0

    # Presas para la tabla (priorizar con volumen + Peñitas salida; incluir escala local al final compacta)
    dams_msnm = [x for x in items if not x.get("nivel_en_escala_local")]
    dams_local = [x for x in items if x.get("nivel_en_escala_local")]
    table_rows = dams_msnm + dams_local[:6]

    fig_h = max(8.5, 4.8 + 0.36 * len(dams_vol) + 0.28 * len(table_rows))
    fig = plt.figure(figsize=(12, fig_h), facecolor=BG_LIGHT)

    _draw_header(
        fig,
        f"REPORTE OPERATIVO DE EMBALSES · {scope_lbl}",
        f"Corte: {fecha} {hora} h (Centro México) · Variación Δ {delta_hours}h · Curvas Oficiales TABLAS EAC",
    )

    pct_color = SEM_RED if tot_pct >= 95 else (SEM_YELLOW if tot_pct >= 85 else CFE_GREEN)
    _draw_kpi_row(fig, [
        ("ALMACENAMIENTO ÚTIL", f"{tot_util:,.1f} Mm³", CFE_GREEN),
        ("CAPACIDAD ÚTIL AL NAMO", f"{tot_cap:,.1f} Mm³", BLUE_MAIN),
        ("LLENADO DEL SISTEMA", f"{tot_pct:.1f}% ({len(dams_vol)} presas)", pct_color),
        ("VOLUMEN LIBRE AL NAMO", f"{tot_libre:,.1f} Mm³", CFE_GOLD),
    ], y_bottom=0.825, height=0.075)

    # Panel superior: Barras horizontales de % llenado útil
    bar_bottom = 0.50 if table_rows else 0.08
    bar_height = 0.295 if table_rows else 0.70
    ax_bar = fig.add_axes([0.21, bar_bottom, 0.75, bar_height], facecolor=CARD_BG)

    if dams_vol:
        sorted_vol = sorted(dams_vol, key=lambda x: x.get("pct_llenado_util") or 0.0)
        labels = [f"{x['estacion']} [{x['clave_cenace']}]" if x.get("clave_cenace") else x["estacion"] for x in sorted_vol]
        pcts = [float(x.get("pct_llenado_util") or 0.0) for x in sorted_vol]
        colors = [_sem_color(x.get("semaforo", "VERDE")) for x in sorted_vol]

        y_pos = list(range(len(sorted_vol)))
        bars = ax_bar.barh(y_pos, pcts, color=colors, height=0.65, edgecolor="none", alpha=0.92)
        ax_bar.set_yticks(y_pos)
        ax_bar.set_yticklabels(labels, fontsize=9, fontweight="bold", color=TEXT_DARK)
        ax_bar.set_xlim(0, max(112, max(pcts) + 15))
        ax_bar.axvline(90, color=SEM_YELLOW, linestyle="--", linewidth=1.2, alpha=0.85, label="Alerta 90%")
        ax_bar.axvline(100, color=SEM_RED, linestyle="--", linewidth=1.4, alpha=0.9, label="NAMO 100%")
        ax_bar.grid(axis="x", linestyle=":", alpha=0.5, color=BORDER_COLOR)
        ax_bar.set_axisbelow(True)
        ax_bar.set_xlabel("% Llenado Útil respecto al NAMO", fontsize=9, color=TEXT_MUTED, fontweight="bold")
        ax_bar.set_title("Porcentaje de Llenado Útil por Embalse (Curvas EAC)", fontsize=10.5, fontweight="bold", color=CFE_GREEN_DARK, loc="left", pad=8)
        ax_bar.legend(loc="lower right", fontsize=8, frameon=True, facecolor="white")

        for bar, x_item in zip(bars, sorted_vol):
            w = bar.get_width()
            u_mm3 = x_item["almacenamiento_util_mm3"]
            ax_bar.text(w + 1.2, bar.get_y() + bar.get_height() / 2,
                        f"{w:.1f}%  ({u_mm3:,.1f} Mm³)", va="center", fontsize=8.3, fontweight="bold", color=TEXT_DARK)
    else:
        ax_bar.text(0.5, 0.5, "Sin embalses con cota msnm en este filtro", ha="center", va="center", color=TEXT_MUTED)
        ax_bar.axis("off")

    # Panel inferior: Tabla ejecutiva
    ax_tbl = fig.add_axes([0.025, 0.03, 0.95, 0.41], facecolor=BG_LIGHT)
    ax_tbl.axis("off")
    ax_tbl.set_title("Detalle Operativo de Cotas, Tendencia y Volúmenes", fontsize=10.5, fontweight="bold", color=CFE_GREEN_DARK, loc="left", pad=6)

    col_labels = ["CRP", "Central / Embalse", "Nivel", f"Δ {delta_hours}h", "Δ NAMO", "Útil (Mm³)", "% Útil", "Libre NAMO"]
    cell_text = []
    cell_colors = []
    for idx, it in enumerate(table_rows):
        row_bg = "#FFFFFF" if idx % 2 == 0 else "#F1F5F9"
        d_m = it.get("delta_m")
        d_str = f"{d_m:+.2f} m" if d_m is not None else "—"
        if it.get("nivel_en_escala_local"):
            lvl_str = f"{it['nivel_actual']:.2f} m*"
            namo_str = "Escala local"
            util_str, pct_str, lib_str = "—", "—", "—"
        elif it.get("nota_namo"):
            lvl_str = f"{it['nivel_actual']:.2f} msnm"
            namo_str = "Salida emb."
            util_str, pct_str, lib_str = "—", "—", "—"
        else:
            lvl_str = f"{it['nivel_actual']:.2f} msnm"
            dn = it.get("delta_namo_m")
            namo_str = f"{dn:+.2f} m" if dn is not None else "—"
            util_str = f"{it['almacenamiento_util_mm3']:,.1f}" if it.get("almacenamiento_util_mm3") is not None else "—"
            pct_str = f"{it['pct_llenado_util']:.1f}%" if it.get("pct_llenado_util") is not None else "—"
            lib_str = f"{it['volumen_disponible_namo_mm3']:,.1f}" if it.get("volumen_disponible_namo_mm3") is not None else "—"

        name_lbl = f"{it['estacion']} [{it['clave_cenace']}]" if it.get("clave_cenace") else it["estacion"]
        cell_text.append([it["crp"], name_lbl[:30], lvl_str, d_str, namo_str, util_str, pct_str, lib_str])

        sem_bg = row_bg
        if it.get("semaforo") == "ROJO":
            sem_bg = "#FEE2E2"
        elif it.get("semaforo") == "AMARILLO":
            sem_bg = "#FEF3C7"
        cell_colors.append([row_bg, row_bg, sem_bg, row_bg, sem_bg, row_bg, sem_bg, row_bg])

    if cell_text:
        tbl = ax_tbl.table(
            cellText=cell_text,
            colLabels=col_labels,
            cellColours=cell_colors,
            colColours=[CFE_GREEN] * len(col_labels),
            loc="upper center",
            cellLoc="center",
            colWidths=[0.10, 0.25, 0.12, 0.09, 0.11, 0.11, 0.09, 0.13],
        )
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(8.3)
        tbl.scale(1.0, 1.38)
        for (r_idx, c_idx), cell in tbl.get_celld().items():
            cell.set_edgecolor(BORDER_COLOR)
            if r_idx == 0:
                cell.set_text_props(color="white", fontweight="bold")
            elif c_idx == 1:
                cell.set_text_props(ha="left")

    if dams_local:
        fig.text(0.025, 0.008, f"* Nota: {len(dams_local)} presa(s) reportan tirante en escala local sin cota msnm.", fontsize=8, color=TEXT_MUTED)

    return _fig_to_png_bytes(fig)


# ==================== 2. LLUVIA 24H POR CUENCA (OPCIÓN 16) ====================
def render_rainfall_basins_chart(cutoff_hour: int = 6) -> bytes:
    data = _fetch_mirror("/api/v1/rainfall-cut/by-basin", {"cutoff_hour": cutoff_hour})
    basins = data.get("data") or []
    rainy = [b for b in basins if (b.get("lamina_max_24h_mm") or 0) > 0][:14]

    fig = plt.figure(figsize=(12, max(6.8, 3.6 + 0.42 * max(len(rainy), 3))), facecolor=BG_LIGHT)
    _draw_header(
        fig,
        "CORTE DE LLUVIA 24 HORAS POR CUENCA · RED GOES CFE",
        f"Fecha corte: {data.get('cutoff_date_local', '')} ({cutoff_hour:02d}:00 h) · Ventana: {data.get('window_local', '')}",
    )

    top_b = max(basins, key=lambda b: b.get("lamina_max_24h_mm") or 0) if basins else {}
    _draw_kpi_row(fig, [
        ("CUENCAS CON LLUVIA", f"{data.get('basins_with_rain', 0)} / {data.get('basins_count', 0)}", BLUE_MAIN),
        ("LÁMINA MÁX. NACIONAL", f"{top_b.get('lamina_max_24h_mm', 0):.1f} mm", BLUE_DARK),
        ("CUENCA MÁXIMA", str(top_b.get("cuenca") or "Sin lluvia")[:22], CFE_GREEN),
        ("ESTACIÓN MÁXIMA", str(top_b.get("estacion_max_nombre") or "—")[:24], CFE_GOLD),
    ], y_bottom=0.80, height=0.09)

    ax = fig.add_axes([0.21, 0.08, 0.74, 0.67], facecolor=CARD_BG)
    if not rainy:
        ax.text(0.5, 0.5, "Sin precipitaciones registradas en las cuencas CFE durante el corte de 24 horas.",
                ha="center", va="center", fontsize=12, fontweight="bold", color=TEXT_MUTED)
        ax.axis("off")
    else:
        rainy_rev = list(reversed(rainy))
        labels = [b["cuenca"] for b in rainy_rev]
        medias = [float(b.get("lamina_media_24h_mm") or 0.0) for b in rainy_rev]
        maxs = [float(b.get("lamina_max_24h_mm") or 0.0) for b in rainy_rev]
        y = list(range(len(rainy_rev)))
        h = 0.36

        b1 = ax.barh([yi - h / 2 for yi in y], medias, height=h, color=BLUE_MAIN, label="Lámina Media 24h (mm)")
        b2 = ax.barh([yi + h / 2 for yi in y], maxs, height=h, color=BLUE_DARK, label="Lámina Máxima 24h (mm)")
        ax.set_yticks(y)
        ax.set_yticklabels(labels, fontsize=9.5, fontweight="bold", color=TEXT_DARK)
        ax.set_xlim(0, max(maxs) * 1.38 + 3)
        ax.grid(axis="x", linestyle=":", alpha=0.5, color=BORDER_COLOR)
        ax.set_axisbelow(True)
        ax.set_xlabel("Precipitación Acumulada 24 horas (mm)", fontsize=9.5, color=TEXT_MUTED, fontweight="bold")
        ax.set_title("Comparativo Lámina Media vs. Lámina Máxima por Cuenca", fontsize=11, fontweight="bold", color=CFE_GREEN_DARK, loc="left", pad=8)
        ax.legend(loc="lower right", fontsize=9, frameon=True, facecolor="white")

        for bar, b_item in zip(b2, rainy_rev):
            w = bar.get_width()
            est = (b_item.get("estacion_max_nombre") or "")[:22]
            ax.text(w + 0.6, bar.get_y() + bar.get_height() / 2, f"{w:.1f} mm ({est})",
                    va="center", fontsize=8.2, fontweight="bold", color=BLUE_DARK)
        for bar in b1:
            w = bar.get_width()
            if w > 0:
                ax.text(w + 0.6, bar.get_y() + bar.get_height() / 2, f"{w:.1f} mm",
                        va="center", fontsize=8.0, color=BLUE_MAIN)

    return _fig_to_png_bytes(fig)


# ==================== 3. BOLETÍN DIARIO INTEGRAL (OPCIÓN 15) ====================
def render_daily_bulletin_infographic(crp: str | None = None, cutoff_hour: int = 6) -> bytes:
    params = {"cutoff_hour": cutoff_hour, "include_text": "false"}
    if crp and crp.upper() != "ALL":
        params["crp"] = crp.upper()
    data = _fetch_mirror("/api/v1/daily-bulletin", params)

    bal = data.get("balance_nacional") or {}
    res_crp = data.get("resumen_crp") or {}
    basins = [b for b in (data.get("lluvia_por_cuenca") or []) if (b.get("lamina_max_24h_mm") or 0) > 0][:8]
    alerts = data.get("alertas_destacadas") or []

    fig = plt.figure(figsize=(12.5, 9.2), facecolor=BG_LIGHT)
    _draw_header(
        fig,
        "BOLETÍN HIDROLÓGICO DIARIO INTEGRAL · CFE",
        f"Corte: {data.get('fecha_corte_local', '')} {cutoff_hour:02d}:00 h · Generado: {data.get('generado_local', '')} h · Ventana: {data.get('ventana_lluvia_24h_local', '')}",
    )

    n_crit = bal.get("alertas_criticas", 0)
    n_prev = bal.get("alertas_preventivas", 0)
    al_col = SEM_RED if n_crit > 0 else (SEM_YELLOW if n_prev > 0 else SEM_GREEN)
    _draw_kpi_row(fig, [
        ("ALMACENAMIENTO ÚTIL", f"{bal.get('almacenamiento_util_mm3', 0):,.1f} Mm³", CFE_GREEN),
        ("LLENADO NACIONAL", f"{bal.get('pct_llenado_nacional', 0) or 0:.1f}% ({bal.get('presas_con_volumen', 0)} presas)", BLUE_MAIN),
        ("DISPONIBLE AL NAMO", f"{bal.get('volumen_disponible_namo_mm3', 0) or 0:,.1f} Mm³", CFE_GOLD),
        ("ALERTAS ACTIVAS", f"{n_crit} Críticas · {n_prev} Prev.", al_col),
    ], y_bottom=0.815, height=0.082)

    # Panel izquierdo: Llenado por CRP
    ax_l = fig.add_axes([0.08, 0.34, 0.39, 0.42], facecolor=CARD_BG)
    crp_items = [(k, v) for k, v in res_crp.items() if v.get("pct_llenado_sistema") is not None]
    if crp_items:
        names = [k for k, _ in crp_items]
        pcts = [float(v["pct_llenado_sistema"]) for _, v in crp_items]
        cols = [SEM_RED if p >= 95 else (SEM_YELLOW if p >= 85 else CFE_GREEN) for p in pcts]
        bars = ax_l.barh(names, pcts, color=cols, height=0.55)
        ax_l.set_xlim(0, 118)
        ax_l.axvline(90, color=SEM_YELLOW, linestyle="--", linewidth=1.0)
        ax_l.axvline(100, color=SEM_RED, linestyle="--", linewidth=1.1)
        ax_l.grid(axis="x", linestyle=":", alpha=0.5)
        ax_l.set_axisbelow(True)
        ax_l.set_title("Llenado Útil por Sistema (CRP)", fontsize=10.5, fontweight="bold", color=CFE_GREEN_DARK, loc="left", pad=8)
        ax_l.set_xlabel("% Llenado respecto al NAMO", fontsize=8.5, color=TEXT_MUTED)
        for bar, (_, v) in zip(bars, crp_items):
            w = bar.get_width()
            ax_l.text(w + 1.5, bar.get_y() + bar.get_height() / 2,
                      f"{w:.1f}% ({v['almacenamiento_util_mm3']:,.0f} Mm³)", va="center", fontsize=8.3, fontweight="bold")
    else:
        ax_l.text(0.5, 0.5, "Sin datos volumétricos", ha="center", va="center")
        ax_l.axis("off")

    # Panel derecho: Top lluvias 24h vs 72h por cuenca
    ax_r = fig.add_axes([0.58, 0.34, 0.39, 0.42], facecolor=CARD_BG)
    if basins:
        b_rev = list(reversed(basins))
        blabs = [b["cuenca"][:18] for b in b_rev]
        m24 = [float(b.get("lamina_max_24h_mm") or 0) for b in b_rev]
        m72 = [float(b.get("lamina_max_72h_mm") or 0) for b in b_rev]
        y = list(range(len(b_rev)))
        ax_r.barh([yi - 0.18 for yi in y], m24, height=0.35, color=BLUE_MAIN, label="Máx 24h (mm)")
        ax_r.barh([yi + 0.18 for yi in y], m72, height=0.35, color="#93C5FD", label="Máx 72h (mm)")
        ax_r.set_yticks(y)
        ax_r.set_yticklabels(blabs, fontsize=8.8, fontweight="bold")
        ax_r.set_xlim(0, max(m72 + m24 + [10]) * 1.25)
        ax_r.grid(axis="x", linestyle=":", alpha=0.5)
        ax_r.set_axisbelow(True)
        ax_r.set_title("Top Cuencas con Precipitación (24h / 72h)", fontsize=10.5, fontweight="bold", color=CFE_GREEN_DARK, loc="left", pad=8)
        ax_r.set_xlabel("Precipitación Máxima (mm)", fontsize=8.5, color=TEXT_MUTED)
        ax_r.legend(loc="lower right", fontsize=8)
    else:
        ax_r.text(0.5, 0.5, "Sin lluvias en las últimas 24h", ha="center", va="center", color=TEXT_MUTED, fontweight="bold")
        ax_r.axis("off")

    # Panel inferior: Alertas destacadas activas
    ax_a = fig.add_axes([0.025, 0.03, 0.95, 0.25], facecolor=CARD_BG)
    ax_a.set_xlim(0, 1)
    ax_a.set_ylim(0, 1)
    ax_a.axis("off")
    ax_a.add_patch(FancyBboxPatch((0.0, 0.0), 1.0, 1.0, boxstyle="round,pad=0.01,rounding_size=0.03",
                                  facecolor=CARD_BG, edgecolor=BORDER_COLOR, linewidth=1.0))
    ax_a.text(0.015, 0.87, f"ALERTAS DESTACADAS ACTIVAS ({len(alerts)})", fontsize=10, fontweight="bold", color=CFE_GREEN_DARK)
    if not alerts:
        ax_a.text(0.015, 0.50, "Operación en normalidad: sin alertas críticas ni preventivas activas.", fontsize=9.5, color=SEM_GREEN, fontweight="bold")
    else:
        y_line = 0.70
        for a in alerts[:5]:
            col = SEM_RED if a.get("severidad") == "CRITICA" else SEM_YELLOW
            sev_tag = "[CRÍTICA]" if a.get("severidad") == "CRITICA" else "[ALERTA]"
            ax_a.text(0.015, y_line, f"{sev_tag} [{a.get('crp')}] {a.get('titulo')} — {a.get('detalle')[:85]}",
                      fontsize=8.6, color=col, fontweight="bold")
            y_line -= 0.145

    return _fig_to_png_bytes(fig)


# ==================== 4. SALUD DE RED HIDROMÉTRICA (OPCIÓN 17) ====================
def render_network_health_dashboard(crp: str | None = None) -> bytes:
    params = {"only_issues": "true", "include_text": "false"}
    if crp and crp.upper() != "ALL":
        params["crp"] = crp.upper()
    data = _fetch_mirror("/api/v1/network-health", params)

    nat = data.get("resumen_nacional") or {}
    by_crp = data.get("resumen_por_crp") or {}
    issues = data.get("estaciones") or []

    fig = plt.figure(figsize=(12.2, 9.0), facecolor=BG_LIGHT)
    _draw_header(
        fig,
        "SALUD DE LA RED HIDROMÉTRICA GOES · QA/QC CFE",
        f"Fecha: {data.get('fecha_local', '')} {data.get('hora_local', '')} h · Monitoreo de transmisión, baterías y sensores",
    )

    disp = nat.get("pct_disponibilidad", 0.0)
    disp_col = SEM_GREEN if disp >= 85 else (SEM_YELLOW if disp >= 70 else SEM_RED)
    _draw_kpi_row(fig, [
        ("DISPONIBILIDAD NACIONAL", f"{disp:.1f}%", disp_col),
        ("ESTACIONES ONLINE", f"{nat.get('online', 0)} / {nat.get('total_estaciones', 0)}", CFE_GREEN),
        ("PRESAS ONLINE", f"{nat.get('presas_online', 0)} / {nat.get('presas_total', 0)}", BLUE_MAIN),
        ("INCIDENCIAS QA/QC", f"Cong: {nat.get('sensores_congelados', 0)} · Bat: {nat.get('baterias_bajas', 0)}", SEM_YELLOW),
    ], y_bottom=0.815, height=0.082)

    # Panel izquierdo: Barras apiladas por CRP
    ax_l = fig.add_axes([0.08, 0.42, 0.50, 0.34], facecolor=CARD_BG)
    crps = sorted(by_crp.keys())
    if crps:
        on_v = [by_crp[c]["online"] for c in crps]
        at_v = [by_crp[c]["atrasadas"] for c in crps]
        off_v = [by_crp[c]["fuera_de_linea"] for c in crps]
        ax_l.barh(crps, on_v, color=SEM_GREEN, label="Online")
        ax_l.barh(crps, at_v, left=on_v, color=SEM_YELLOW, label="Atrasadas")
        ax_l.barh(crps, off_v, left=[a + b for a, b in zip(on_v, at_v)], color=SEM_RED, label="Fuera de línea")
        ax_l.grid(axis="x", linestyle=":", alpha=0.5)
        ax_l.set_axisbelow(True)
        ax_l.set_title("Estado de Transmisión por CRP", fontsize=10.5, fontweight="bold", color=CFE_GREEN_DARK, loc="left", pad=8)
        ax_l.set_xlabel("Número de Estaciones", fontsize=8.5, color=TEXT_MUTED)
        ax_l.legend(loc="lower right", fontsize=8)
    else:
        ax_l.axis("off")

    # Panel derecho: Dona de disponibilidad global
    ax_r = fig.add_axes([0.63, 0.42, 0.34, 0.34], facecolor=CARD_BG)
    vals = [nat.get("online", 0), nat.get("atrasadas", 0), nat.get("fuera_de_linea", 0)]
    if sum(vals) > 0:
        ax_r.pie(
            vals,
            colors=[SEM_GREEN, SEM_YELLOW, SEM_RED],
            startangle=90,
            wedgeprops={"width": 0.38, "edgecolor": "white"},
        )
        ax_r.text(0, 0.05, f"{disp:.1f}%", ha="center", va="center", fontsize=18, fontweight="bold", color=TEXT_DARK)
        ax_r.text(0, -0.18, "ONLINE", ha="center", va="center", fontsize=9, fontweight="bold", color=TEXT_MUTED)
        ax_r.set_title("Disponibilidad Global", fontsize=10.5, fontweight="bold", color=CFE_GREEN_DARK, pad=6)

    # Panel inferior: Tabla de incidencias prioritarias (top 10)
    ax_t = fig.add_axes([0.025, 0.03, 0.95, 0.33], facecolor=BG_LIGHT)
    ax_t.axis("off")
    ax_t.set_title(f"Incidencias Prioritarias ({min(len(issues), 10)} de {len(issues)})", fontsize=10.5, fontweight="bold", color=CFE_GREEN_DARK, loc="left", pad=5)

    top_iss = issues[:10]
    if top_iss:
        cols = ["CRP", "Estación", "ID", "Estado", "Últ. dato", "Incidencia(s)"]
        rows_t = []
        rcols = []
        for idx, x in enumerate(top_iss):
            bg = "#FFFFFF" if idx % 2 == 0 else "#F1F5F9"
            st_bg = "#FEE2E2" if x["estado"] == "FUERA_DE_LINEA" else ("#FEF3C7" if x["estado"] == "ATRASADA" else "#DCFCE7")
            age_s = f"{x['horas_sin_datos']}h" if x.get("horas_sin_datos") is not None else ">72h"
            rows_t.append([
                x["crp"],
                (x["estacion"] or "")[:24],
                (x.get("id_asignado") or "")[:14],
                x["estado"],
                age_s,
                ", ".join(x.get("problemas") or [])[:34],
            ])
            rcols.append([bg, bg, bg, st_bg, bg, bg])
        tbl = ax_t.table(
            cellText=rows_t,
            colLabels=cols,
            cellColours=rcols,
            colColours=[CFE_GREEN] * len(cols),
            loc="upper center",
            cellLoc="center",
            colWidths=[0.11, 0.24, 0.13, 0.14, 0.09, 0.29],
        )
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(8.2)
        tbl.scale(1.0, 1.32)
        for (r_i, c_i), cell in tbl.get_celld().items():
            cell.set_edgecolor(BORDER_COLOR)
            if r_i == 0:
                cell.set_text_props(color="white", fontweight="bold")
            elif c_i in (1, 5):
                cell.set_text_props(ha="left")
    else:
        ax_t.text(0.5, 0.5, "Sin incidencias activas en la red hidrométrica.", ha="center", va="center", color=SEM_GREEN, fontweight="bold")

    return _fig_to_png_bytes(fig)


# ==================== 5. HIDROGRAMA ACOPLADO 72H POR PRESA ====================
def render_dam_hydrograph_72h(dam_query: str) -> tuple[bytes, str]:
    q_norm = _strip_accents(dam_query).strip()
    wl = _fetch_mirror("/api/v1/water-levels", {"only_dams": "true", "delta_hours": 3, "include_text": "false"})
    dams = wl.get("data") or []

    match = None
    for d in dams:
        hay = " ".join([
            _strip_accents(d.get("estacion") or ""),
            _strip_accents(d.get("central") or ""),
            _strip_accents(d.get("clave_cenace") or ""),
            _strip_accents(d.get("id_asignado") or ""),
        ])
        if q_norm and q_norm in hay:
            match = d
            break

    if not match:
        raise ValueError(f"No se encontró la presa '{dam_query}'. Prueba con: Angostura, Chicoasén, Malpaso, Peñitas, Infiernillo, Temascal, Aguamilpa, Santa Rosa.")

    id_asig = match.get("id_asignado")
    hr = _fetch_mirror("/api/v1/hourly", {"id_asignado": id_asig, "hours": 72, "format": "grouped"})
    st_data = (hr.get("data") or [{}])[0]
    vars_map = st_data.get("variables") or {}

    niv_raw = vars_map.get("nivel_de_agua") or []
    rain_raw = vars_map.get("precipitación") or vars_map.get("precipitacion") or []

    niv_ts, niv_vals = [], []
    for p in niv_raw:
        v = p.get("valor")
        if v is None or v <= -700 or v >= 99999:
            continue
        dt_utc = datetime.datetime.fromisoformat(p["ts_utc"].replace("Z", ""))
        niv_ts.append(dt_utc - datetime.timedelta(hours=6))
        niv_vals.append(float(v))

    rain_ts, rain_vals = [], []
    for p in rain_raw:
        v = p.get("valor")
        if v is None or v < 0 or v >= 300:
            continue
        dt_utc = datetime.datetime.fromisoformat(p["ts_utc"].replace("Z", ""))
        rain_ts.append(dt_utc - datetime.timedelta(hours=6))
        rain_vals.append(float(v))

    if not niv_ts:
        raise ValueError(f"Sin serie horaria de nivel en las últimas 72h para {match['estacion']}.")

    fig = plt.figure(figsize=(12, 7.2), facecolor=BG_LIGHT)
    tag = f"[{match['clave_cenace']}]" if match.get("clave_cenace") else ""
    _draw_header(
        fig,
        f"HIDROGRAMA ACOPLADO 72 HORAS · {match['estacion'].upper()} {tag}",
        f"CRP: {match['crp']} · ID: {id_asig} · Última lectura: {match['ts_local']} h (Centro México)",
    )

    pct_s = f"{match['pct_llenado_util']:.1f}%" if match.get("pct_llenado_util") is not None else "N/A"
    util_s = f"{match['almacenamiento_util_mm3']:,.1f} Mm³" if match.get("almacenamiento_util_mm3") is not None else "Escala / Salida"
    dn = match.get("delta_namo_m")
    dn_s = f"{dn:+.2f} m" if dn is not None else "N/A"
    dm = match.get("delta_m")
    dm_s = f"{dm:+.2f} m" if dm is not None else "—"

    _draw_kpi_row(fig, [
        ("NIVEL ACTUAL", f"{match['nivel_actual']:.2f} {match['unidad']}", CFE_GREEN),
        ("VARIACIÓN Δ 3H", dm_s, BLUE_MAIN),
        ("DIFERENCIA AL NAMO", dn_s, _sem_color(match.get("semaforo", "VERDE"))),
        ("ALMACENAMIENTO ÚTIL", f"{util_s} ({pct_s})", CFE_GOLD),
    ], y_bottom=0.80, height=0.09)

    ax1 = fig.add_axes([0.09, 0.12, 0.82, 0.63], facecolor=CARD_BG)
    ax1.plot(niv_ts, niv_vals, color=CFE_GREEN, linewidth=2.4, label=f"Nivel ({match['unidad']})")
    ax1.fill_between(niv_ts, min(niv_vals), niv_vals, color=CFE_GREEN, alpha=0.12)

    ymin, ymax = min(niv_vals), max(niv_vals)
    span = max(0.5, ymax - ymin)
    namo = match.get("namo_msnm")
    name = match.get("name_msnm")
    if not match.get("nivel_en_escala_local") and not match.get("nota_namo"):
        if namo is not None and abs(namo - ymax) <= max(20.0, span * 5):
            ax1.axhline(namo, color=SEM_RED, linestyle="--", linewidth=1.5, label=f"NAMO ({namo:.2f} msnm)")
            ymax = max(ymax, namo)
        if name is not None and abs(name - ymax) <= max(25.0, span * 6):
            ax1.axhline(name, color="#7C3AED", linestyle=":", linewidth=1.4, label=f"NAME ({name:.2f} msnm)")
            ymax = max(ymax, name)

    ax1.set_ylim(ymin - span * 0.25, ymax + span * 0.45)
    ax1.set_ylabel(f"Nivel de Agua ({match['unidad']})", fontsize=9.5, fontweight="bold", color=CFE_GREEN_DARK)
    ax1.grid(True, linestyle=":", alpha=0.5, color=BORDER_COLOR)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m %H:%M"))
    fig.autofmt_xdate(rotation=25)

    # Hietograma invertido arriba en eje derecho si hay lluvia > 0
    total_rain = sum(rain_vals)
    if rain_ts and total_rain > 0:
        ax2 = ax1.twinx()
        ax2.bar(rain_ts, rain_vals, width=0.03, color=BLUE_MAIN, alpha=0.55, label=f"Lluvia (72h: {total_rain:.1f} mm)")
        ax2.set_ylim(max(rain_vals) * 3.5, 0)
        ax2.set_ylabel("Precipitación (mm)", fontsize=9, fontweight="bold", color=BLUE_MAIN)
        ax2.legend(loc="upper right", fontsize=8.5)

    ax1.legend(loc="lower left", fontsize=8.5, frameon=True, facecolor="white")
    caption = f"📈 *Hidrograma 72h — {match['estacion']} {tag}* | Nivel: {match['nivel_actual']:.2f} {match['unidad']} | Δ 3h: {dm_s} | Útil: {util_s} ({pct_s})"
    return _fig_to_png_bytes(fig), caption
