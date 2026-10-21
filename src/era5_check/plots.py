"""PNG charts for the README (``docs/img``), drawn from the result tables.

Usage:
    write_all(tables, Path("docs/img"))
"""
from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm  # noqa: E402
from matplotlib.ticker import FixedLocator, FuncFormatter  # noqa: E402

from era5_check import energy  # noqa: E402

INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
DIVERGING = LinearSegmentedColormap.from_list("bias", ["#184f95", "#6da7ec", "#f0efec", "#ec8a89", "#b42323"])
TERRAIN_MARKER = {"coast": "o", "lowland": "s", "upland": "^", "summit": "D"}
TERRAIN_ORDER = ["coast", "lowland", "upland", "summit"]
# label offsets [points] for stations that sit close together on the map
MAP_OFFSET = {650: (6, -11), 625: (6, 3), 510: (-24, -9), 500: (-22, 4), 415: (6, 3), 520: (6, -9)}
METHOD_LABEL = {"raw": "raw ERA5", "linear": "linear regression", "variance": "variance ratio",
                "matrix": "matrix method"}

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": INK2, "axes.labelcolor": INK, "xtick.color": INK2, "ytick.color": INK2,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8, "axes.axisbelow": True,
    "axes.spines.top": False, "axes.spines.right": False, "font.size": 9.5, "axes.titlesize": 10.5,
    "axes.titleweight": "bold", "axes.titlelocation": "left", "legend.frameon": False,
    "lines.linewidth": 2.0, "figure.dpi": 110,
})


def poland_outline() -> tuple[list[float], list[float]]:
    data = json.loads(resources.files("era5_check").joinpath("data", "poland_outline.json").read_text())
    return data["lon"], data["lat"]


def _save(fig, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, bbox_inches="tight", dpi=130)
    plt.close(fig)
    return path


def bias_map(energy_t: pd.DataFrame, path: Path) -> Path:
    lon, lat = poland_outline()
    panels = [("wind_speed_error_pct", "Wind speed at 10 m: ERA5 minus station [%]", 75),
              ("ghi_error_pct", "Global horizontal irradiance, annual sum [%]", 12)]
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.6))
    for ax, (col, title, lim) in zip(axes, panels):
        ax.fill(lon, lat, color="#f0efec", edgecolor=INK2, linewidth=0.8, zorder=1)
        norm = TwoSlopeNorm(vmin=-lim, vcenter=0.0, vmax=lim)
        sc = ax.scatter(energy_t["lon"], energy_t["lat"], c=energy_t[col].clip(-lim, lim), cmap=DIVERGING,
                        norm=norm, s=70, edgecolor=SURFACE, linewidth=1.5, zorder=3)
        for _, r in energy_t.iterrows():
            ax.annotate(f"{r[col]:+.0f}", (r["lon"], r["lat"]), xytext=MAP_OFFSET.get(int(r["code"]), (5, 4)),
                        textcoords="offset points", fontsize=7.5, color=INK)
        ax.set_title(title)
        ax.set_aspect(1.0 / np.cos(np.radians(52.0)))
        ax.grid(False)
        ax.set_xticks([])
        ax.set_yticks([])
        for s in ax.spines.values():
            s.set_visible(False)
        cb = fig.colorbar(sc, ax=ax, shrink=0.75, pad=0.02)
        cb.outline.set_visible(False)
        cb.ax.tick_params(labelsize=8)
    fig.text(0.01, 0.06, "Red: ERA5 higher than the measurement, blue: lower. Colour scale clipped at the "
             "panel limit; labels show the value.", fontsize=8, color=INK2)
    return _save(fig, path)


def bias_vs_terrain(energy_t: pd.DataFrame, path: Path) -> Path:
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 3.9), sharey=True)
    for ax, col, label in ((axes[0], "elev_m", "station elevation [m a.s.l.]"),
                           (axes[1], "sea_km", "distance to the Baltic coast [km]")):
        for terrain in TERRAIN_ORDER:
            sub = energy_t[energy_t["terrain"] == terrain]
            ax.scatter(sub[col], sub["wind_speed_error_pct"], marker=TERRAIN_MARKER[terrain], s=46,
                       color=BLUE, edgecolor=SURFACE, linewidth=1.2, label=terrain, zorder=3)
        ax.axhline(0, color=INK2, linewidth=1)
        ax.set_xlabel(label)
        for _, r in energy_t.iterrows():
            if abs(r["wind_speed_error_pct"]) > 40 or r["terrain"] in ("summit", "coast"):
                left = r[col] > 0.8 * energy_t[col].max()
                ax.annotate(r["name"], (r[col], r["wind_speed_error_pct"]), xytext=(-6 if left else 6, -3),
                            textcoords="offset points", fontsize=7.5, color=INK2, ha="right" if left else "left")
    axes[0].set_xscale("symlog", linthresh=100)
    axes[0].xaxis.set_major_locator(FixedLocator([0, 50, 100, 300, 1000]))
    axes[0].set_xlim(-5, 3500)
    axes[0].xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}"))
    axes[0].set_ylabel("ERA5 wind speed bias [%]")
    axes[0].set_title("Bias against elevation")
    axes[1].set_title("Bias against distance to the sea")
    axes[0].legend(loc="lower left", fontsize=8)
    return _save(fig, path)


def daily_cycle(cycle: pd.DataFrame, season: pd.DataFrame, energy_t: pd.DataFrame, path: Path) -> Path:
    terrain = energy_t.set_index("code")["terrain"]
    fig, axes = plt.subplots(2, 2, figsize=(10.5, 6.4))
    colors = {"coast": AQUA, "lowland": BLUE, "upland": ORANGE, "summit": INK2}
    w = cycle[(cycle["sample"] == "wind") & (cycle["model"] == "era5")].copy()
    w["terrain"] = w["code"].map(terrain)
    ax = axes[0, 0]
    for t in TERRAIN_ORDER[:3]:
        g = w[w["terrain"] == t].groupby("hour_utc")["bias"].mean()
        ax.plot(g.index, g.values, color=colors[t], label=t)
    summit = w[w["terrain"] == "summit"].groupby("hour_utc")["bias"].mean()
    ax.axhline(0, color=INK2, linewidth=1)
    ax.set_title("Wind speed bias by hour [m/s], mean of stations")
    ax.text(0.98, 0.62, f"summits (not drawn):\n{summit.min():.1f} to {summit.max():.1f} m/s", transform=ax.transAxes,
            fontsize=8, color=INK2, ha="right")
    ax.set_xlabel("hour (UTC)")
    ax.legend(fontsize=8, ncol=2)
    g = cycle[(cycle["sample"] == "ghi_hourly") & (cycle["model"] == "era5")]
    piv = g.pivot(index="hour_utc", columns="code", values="bias")
    piv = piv[piv.notna().sum(axis=1) >= 10]
    ax = axes[0, 1]
    ax.fill_between(piv.index, piv.quantile(0.1, axis=1), piv.quantile(0.9, axis=1), color=BLUE, alpha=0.18,
                    linewidth=0, label="10th-90th percentile of stations")
    ax.plot(piv.index, piv.median(axis=1), color=BLUE, label="median station")
    ax.axhline(0, color=INK2, linewidth=1)
    ax.set_title("GHI bias by hour, sun above 5 deg [W/m2]")
    ax.set_xlabel("hour (UTC)")
    ax.legend(fontsize=8)
    months = np.arange(1, 13)
    for ax, sample, title in ((axes[1, 0], "wind", "Wind speed bias by month [%]"),
                              (axes[1, 1], "ghi_daily", "Daily GHI bias by month [%]")):
        s = season[(season["sample"] == sample) & (season["model"] == "era5")].copy()
        s["terrain"] = s["code"].map(terrain)
        if sample == "wind":
            for t in TERRAIN_ORDER[:3]:
                m = s[s["terrain"] == t].groupby("month")["rel_bias_pct"].median().reindex(months)
                ax.plot(months, m.values, color=colors[t], label=t, marker="o", markersize=3.5)
            m = s[s["terrain"] == "summit"]["rel_bias_pct"]
            ax.text(0.02, 0.93, f"summits (not drawn): {m.min():.0f} to {m.max():.0f} %", transform=ax.transAxes,
                    fontsize=8, color=INK2)
        else:
            piv = s.pivot(index="month", columns="code", values="rel_bias_pct").reindex(months)
            ax.fill_between(months, piv.quantile(0.1, axis=1), piv.quantile(0.9, axis=1), color=BLUE,
                            alpha=0.18, linewidth=0)
            ax.plot(months, piv.median(axis=1), color=BLUE, marker="o", markersize=3.5)
        ax.axhline(0, color=INK2, linewidth=1)
        ax.set_xticks(months, ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"])
        ax.set_title(title)
    axes[1, 0].set_title("Wind speed bias by month [%], median station")
    axes[1, 1].set_title("Daily GHI bias by month [%], median and 10th-90th percentile")
    fig.tight_layout()
    return _save(fig, path)


def energy_impact(energy_t: pd.DataFrame, path: Path) -> Path:
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.2))
    ax = axes[0]
    ref = np.linspace(-0.8, 0.8, 161)
    rng = np.random.default_rng(0)
    base = rng.weibull(2.0, 100_000)
    base *= energy.HUB_MEAN_MS / base.mean()
    cf0 = energy.capacity_factor(base)
    curve = [100 * (energy.capacity_factor(base * (1 + r)) / cf0 - 1) for r in ref]
    ax.plot(100 * ref, curve, color=INK2, linewidth=1.2, linestyle="--",
            label="same error on a Weibull k=2, 7 m/s site")
    for terrain in TERRAIN_ORDER:
        sub = energy_t[energy_t["terrain"] == terrain]
        ax.scatter(sub["wind_speed_error_pct"], sub["wind_energy_error_pct"], marker=TERRAIN_MARKER[terrain],
                   s=46, color=BLUE, edgecolor=SURFACE, linewidth=1.2, label=terrain, zorder=3)
    ax.axhline(0, color=INK2, linewidth=1)
    ax.axvline(0, color=INK2, linewidth=1)
    ax.set_xlabel("ERA5 wind speed error [%]")
    ax.set_ylabel("energy error, MM92 at 7 m/s hub mean [%]")
    ax.set_title("Wind: speed error -> energy error")
    ax.legend(fontsize=7.5, loc="upper left")
    ax = axes[1]
    for terrain in TERRAIN_ORDER:
        sub = energy_t[energy_t["terrain"] == terrain]
        ax.scatter(sub["ghi_error_pct"], sub["pv_error_pct"], marker=TERRAIN_MARKER[terrain], s=46, color=ORANGE,
                   edgecolor=SURFACE, linewidth=1.2, label=terrain, zorder=3)
    lim = np.nanmax(np.abs(energy_t[["ghi_error_pct", "pv_error_pct"]].to_numpy())) * 1.15
    ax.plot([-lim, lim], [-lim, lim], color=INK2, linewidth=1.2, linestyle="--", label="PV error = GHI error")
    ax.axhline(0, color=INK2, linewidth=1)
    ax.axvline(0, color=INK2, linewidth=1)
    big = energy_t[energy_t["pv_error_pct"].abs() > 8].sort_values("pv_error_pct")
    for i, (_, r) in enumerate(big.iterrows()):
        ax.annotate(r["name"], (r["ghi_error_pct"], r["pv_error_pct"]), xytext=(-8, 6 * (i - 1) - 3),
                    textcoords="offset points", fontsize=7.5, color=INK2, ha="right")
    ax.set_xlabel("ERA5 GHI error, annual sum [%]")
    ax.set_ylabel("PV yield error, 30 deg south [%]")
    ax.set_title("PV: irradiance error -> yield error")
    ax.legend(fontsize=7.5, loc="upper left")
    fig.tight_layout()
    return _save(fig, path)


def mcp_summary(summary: pd.DataFrame, n_stations: int, path: Path) -> Path:
    """``summary``: rows (scheme, method) with mean absolute energy error and mean Wasserstein distance."""
    methods = ["raw", "linear", "variance", "matrix"]
    schemes = [("leave_one_year_out", "leave-one-year-out (train 5 y, test 1 y)", BLUE),
               ("one_year_campaign", "one-year campaign (train 1 y, test 5 y)", ORANGE)]
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 3.8))
    x = np.arange(len(methods))
    for ax, col, title in ((axes[0], "abs_energy_error_pct", "Mean absolute energy error [%]"),
                           (axes[1], "wasserstein_ms", "Distance between distributions [m/s]")):
        for i, (scheme, label, color) in enumerate(schemes):
            vals = summary.loc[scheme].reindex(methods)[col].to_numpy()
            bars = ax.bar(x + (i - 0.5) * 0.36, vals, width=0.34, color=color, label=label)
            ax.bar_label(bars, fmt="%.1f" if col.startswith("abs") else "%.2f", fontsize=7.5, color=INK, padding=2)
        ax.set_xticks(x, [METHOD_LABEL[m] for m in methods])
        ax.set_title(title)
        ax.set_yscale("log")
        ticks = [1, 2, 5, 10, 20, 50] if col.startswith("abs") else [0.1, 0.2, 0.5, 1.0, 2.0]
        ax.yaxis.set_major_locator(FixedLocator(ticks))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
        ax.yaxis.set_minor_locator(FixedLocator([]))
    axes[0].legend(fontsize=7.5, loc="upper right")
    fig.text(0.01, -0.03, f"Mean over {n_stations} stations and all folds; log scale. Wasserstein (earth "
             "mover's) distance between the predicted and measured speed distributions.",
             fontsize=8, color=INK2)
    fig.tight_layout()
    return _save(fig, path)


def kelmarsh_figure(pair_hist: dict, kel: pd.DataFrame, path: Path) -> Path:
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 3.8))
    ax = axes[0]
    ax.bar(pair_hist["speed"], pair_hist["obs"], width=0.45, color=BLUE, label="nacelle anemometers", align="edge")
    ax.bar(np.asarray(pair_hist["speed"]) + 0.5, pair_hist["era5"], width=0.45, color=ORANGE,
           label="ERA5 at 78.5 m", align="edge")
    ax.set_xlabel("hourly wind speed [m/s]")
    ax.set_ylabel("share of hours [%]")
    ax.set_title("Kelmarsh 2016-2021: hub-height wind distribution")
    ax.legend(fontsize=8)
    ax = axes[1]
    cells = [[f"{r['rel_bias_pct']:+.1f} %", f"{r['rmse']:.2f}", f"{r['r2']:.2f}", f"{r['energy_error_pct']:+.1f} %"]
             for _, r in kel.iterrows()]
    ax.axis("off")
    table = ax.table(cellText=cells, rowLabels=kel["series"].tolist(),
                     colLabels=["speed bias", "RMSE [m/s]", "R2", "energy error"], loc="center", cellLoc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(8.5)
    table.scale(1.0, 1.6)
    for (r, c), cell in table.get_celld().items():
        cell.set_edgecolor(GRID)
        cell.set_facecolor(SURFACE)
    ax.set_title("Hourly scores against the farm-mean nacelle wind", pad=14)
    fig.tight_layout()
    return _save(fig, path)


def example_station(payload: dict, name: str, path: Path) -> Path:
    p = payload["era5"]
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.0))
    ax = axes[0]
    counts = np.asarray(p["wind_counts"], dtype=float)
    counts[counts == 0] = np.nan
    ax.pcolormesh(np.r_[np.asarray(p["wind_obs"]) - 0.5, p["wind_obs"][-1] + 0.5],
                  np.r_[np.asarray(p["wind_model"]) - 0.25, p["wind_model"][-1] + 0.25], np.log10(counts),
                  cmap=LinearSegmentedColormap.from_list("seq", ["#cde2fb", "#3987e5", "#0d366b"]), shading="flat")
    top = max(p["wind_obs"][-1], 8)
    ax.plot([0, top], [0, top], color=INK2, linewidth=1, linestyle="--")
    ax.set_xlabel("station wind speed [m/s, whole m/s]")
    ax.set_ylabel("ERA5 10 m wind speed [m/s]")
    ax.set_title(f"{name}: hourly wind, log count")
    ax = axes[1]
    ax.scatter(p["ghi_obs"], p["ghi_model"], s=6, color=ORANGE, alpha=0.45, linewidth=0)
    top = max(max(p["ghi_obs"]), max(p["ghi_model"]))
    ax.plot([0, top], [0, top], color=INK2, linewidth=1, linestyle="--")
    ax.set_xlabel("station daily GHI [kWh/m2]")
    ax.set_ylabel("ERA5 daily GHI [kWh/m2]")
    ax.set_title(f"{name}: daily irradiation")
    fig.tight_layout()
    return _save(fig, path)


MODEL_LABEL = {"era5": "ERA5 (0.25 deg)", "cerra": "CERRA (5.5 km)"}
MODEL_COLOR = {"era5": BLUE, "cerra": ORANGE}


def model_comparison(tables: dict[str, pd.DataFrame], models: list[str], path: Path) -> Path:
    """Per-station bias of each reanalysis for wind and GHI, wind R2, and the GHI daily cycle."""
    e = tables["energy"]
    wide = e.pivot(index="code", columns="model", values=["wind_speed_error_pct", "ghi_error_pct"])
    names = e.drop_duplicates("code").set_index("code")["name"]
    fig, axes = plt.subplots(2, 2, figsize=(10.5, 9.0), gridspec_kw={"height_ratios": [1.5, 1.0]})
    for ax, col, title in ((axes[0, 0], "wind_speed_error_pct", "Wind speed bias at 10 m [%]"),
                           (axes[0, 1], "ghi_error_pct", "GHI bias, annual sum [%]")):
        d = wide[col].sort_values(models[0])
        y = np.arange(len(d))
        ax.hlines(y, d.min(axis=1), d.max(axis=1), color=GRID, linewidth=2.5, zorder=1)
        for m, marker in zip(models, ("o", "D")):
            ax.scatter(d[m], y, color=MODEL_COLOR[m], marker=marker, s=36, edgecolor=SURFACE, linewidth=1.2,
                       label=MODEL_LABEL[m], zorder=3)
        ax.axvline(0, color=INK2, linewidth=1)
        ax.set_yticks(y, [names[c] for c in d.index], fontsize=8)
        ax.grid(axis="y", visible=False)
        ax.set_title(title)
    axes[0, 0].legend(fontsize=8, loc="lower right")
    sc = tables["scores"]
    r2 = sc[sc["sample"] == "wind"].pivot(index="code", columns="model", values="r2")
    terrain = e.drop_duplicates("code").set_index("code")["terrain"]
    ax = axes[1, 0]
    for t in TERRAIN_ORDER:
        idx = terrain[terrain == t].index
        ax.scatter(r2.loc[idx, models[0]], r2.loc[idx, models[1]], marker=TERRAIN_MARKER[t], s=40, color=INK2,
                   edgecolor=SURFACE, linewidth=1.2, label=t, zorder=3)
    ax.plot([0, 1], [0, 1], color=INK2, linewidth=1, linestyle="--")
    ax.set_xlim(0.1, 0.8)
    ax.set_ylim(0.1, 0.8)
    ax.set_xlabel(f"{MODEL_LABEL[models[0]]} hourly wind R2")
    ax.set_ylabel(f"{MODEL_LABEL[models[1]]} hourly wind R2")
    ax.set_title("Hourly wind R2 per station")
    ax.legend(fontsize=8, loc="lower right")
    ax = axes[1, 1]
    cyc = tables["cycle"][tables["cycle"]["sample"] == "ghi_hourly"]
    for m in models:
        piv = cyc[cyc["model"] == m].pivot(index="hour_utc", columns="code", values="bias")
        piv = piv[piv.notna().sum(axis=1) >= 10]
        ax.plot(piv.index, piv.median(axis=1), color=MODEL_COLOR[m], label=MODEL_LABEL[m], marker="o", markersize=3.5)
    ax.axhline(0, color=INK2, linewidth=1)
    ax.set_xlabel("hour (UTC)")
    ax.set_title("GHI bias by hour, median station [W/m2]")
    ax.legend(fontsize=8)
    fig.tight_layout()
    return _save(fig, path)
