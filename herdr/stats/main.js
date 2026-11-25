// Stats view: cards with column charts, fed by cached GitHub data.
//  1. my merged PRs per day, current month
//  2. lines added per month, last 12 months (merged PRs, all repos)
import { View, div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { exec, theme, appHome } from "herdr";

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const SHORT = MONTHS.map((m) => m.slice(0, 3));

// Each source: a script that prints JSON, how often to run it, and what makes a redraw worthwhile.
const SOURCES = {
  merges: { script: "fetch-merges.sh", every: 5 * 60_000, sig: (d) => [d.days, d.today] },
  loc: { script: "fetch-loc.sh", every: 30 * 60_000, sig: (d) => d.months },
  cycle: { script: "fetch-cycle.sh", every: 15 * 60_000, sig: (d) => [d.first_commit, d.spawn] },
  quality: { script: "fetch-quality.sh", every: 15 * 60_000, sig: (d) => [d.prs, d.rejections, d.first_try] },
  agents: { script: "fetch-agents.sh", every: 30 * 60_000, sig: (d) => [d.tasks, d.cost_total] },
  ale: { script: "fetch-ale.sh", every: 10 * 60_000, sig: (d) => [d.n, d.total, d.rows.map((r) => [r.total, r.rounds])] },
};

// 22.8 -> "22.8h", 41.8 -> "1d 18h", 0.5 -> "30m"
function dur(h) {
  if (h == null) return "–";
  if (h < 1) return `${Math.round(h * 60)}m`;
  if (h < 24) return `${h.toFixed(1)}h`;
  return `${Math.floor(h / 24)}d ${Math.round(h % 24)}h`;
}

const compact = (n) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(n));

export default class Stats extends View {
  init(_props, cx) {
    this.data = {};
    this.errors = {};
    this.sigs = {};
    for (const [name, src] of Object.entries(SOURCES)) {
      cx.timer.every(src.every, (cx) => this.refresh(name, cx));
      this.refresh(name, cx);
    }
  }

  refresh(name, cx) {
    cx.spawn(async (cx) => {
      const src = SOURCES[name];
      try {
        const r = await exec("sh", [`${appHome()}/mods/stats/${src.script}`]);
        if (!r.stdout.trim()) throw new Error(r.stderr.trim() || "empty response");
        const d = JSON.parse(r.stdout);
        const sig = JSON.stringify(src.sig(d));
        const same = sig === this.sigs[name] && !this.errors[name];
        this.sigs[name] = sig;
        this.data[name] = d;
        this.errors[name] = null;
        if (same) return; // nothing new: no redraw
      } catch (e) {
        this.errors[name] = String(e.message ?? e).slice(0, 200);
      }
      cx.notify();
    });
  }

  // A column chart: values[i] as a bar, label under it, count above it.
  columns(values, labels, { plotH, id, bold = -1, fmt = String }, t) {
    const max = Math.max(1, ...values);
    return v_flex().id(id)
      .child(
        h_flex().gap(4).items_end().px(8).pt(8).border_b(1).border_color(t.border)
          .children(values.map((n, i) =>
            v_flex().id(`${id}-${i}`).flex_1().min_w(0).items_center().justify_end().gap(4)
              .child(div().text_size(11).text_color(n ? t.foreground : t.dim).child(n ? fmt(n) : ""))
              .child(div().w_full().h(Math.max(n ? 3 : 0, Math.round((n / max) * plotH))).rounded(2).bg(t.accent)))),
      )
      .child(
        h_flex().gap(4).px(8).pt(4)
          .children(labels.map((l, i) =>
            div().flex_1().min_w(0).text_size(10).text_color(i === bold ? t.foreground : t.muted)
              .when(i === bold, (e) => e.font_semibold()).child(l))),
      );
  }

  card(title, sub, body, t) {
    return v_flex().gap(10).p(16).rounded(6).bg(t.surface).border(1).border_color(t.border)
      .child(v_flex().gap(2)
        .child(div().text_size(14).font_semibold().text_color(t.foreground).child(title))
        .child(sub))
      .when(body, (e) => e.child(body));
  }

  sub(name, text, t) {
    return this.errors[name]
      ? div().text_size(12).text_color(t.blocked).child(`GitHub error: ${this.errors[name]}`)
      : div().text_size(12).text_color(t.dim).child(this.data[name] ? text(this.data[name]) : "loading…");
  }

  mergesCard(t) {
    const d = this.data.merges;
    const title = d ? `Merged PRs per day · ${MONTHS[d.month - 1]} ${d.year}` : "Merged PRs per day";
    const sub = this.sub("merges", (d) => `${d.total} merged this month · mine · updated ${new Date(d.fetched_at).toLocaleTimeString()}`, t);
    const body = d && this.columns(d.days, d.days.map((_, i) => String(i + 1)), { plotH: 120, id: "merges", bold: d.today - 1 }, t);
    return this.card(title, sub, body, t);
  }

  locCard(t) {
    const d = this.data.loc;
    const sub = this.sub("loc", (d) => `${d.total_additions.toLocaleString()} lines added · ${d.total_prs} merged PRs · mine, all repos · updated ${new Date(d.fetched_at).toLocaleTimeString()}`, t);
    const body = d && this.columns(
      d.months.map((m) => m.additions),
      d.months.map((m) => SHORT[m.month - 1]),
      { plotH: 120, id: "loc", bold: d.months.length - 1, fmt: compact }, t);
    return this.card("Lines added per month · last 12 months", sub, body, t);
  }

  tile(label, value, t) {
    return v_flex().flex_1().min_w(0).gap(2).p(12).rounded(4).bg(t.canvas).border(1).border_color(t.border)
      .child(div().text_size(11).text_color(t.muted).child(label))
      .child(div().text_size(24).font_semibold().text_color(t.foreground).child(value));
  }

  metrics(m, t) {
    return h_flex().gap(10)
      .child(this.tile("AVERAGE", dur(m.avg_h), t))
      .child(this.tile("MEDIAN", dur(m.median_h), t))
      .child(this.tile("P90", dur(m.p90_h), t))
      .child(this.tile("PRS", String(m.n), t));
  }

  fmtPct(x) { return x == null ? "–" : `${Math.round(x * 100)}%`; }
  usd(x) { return x == null ? "–" : `$${x.toFixed(x >= 100 ? 0 : 2)}`; }

  qualityCard(t) {
    const d = this.data.quality;
    const sub = this.sub("quality", (d) => `my PRs merged in ${this.monthName(d)} · rejection = a "changes requested" review from someone else · updated ${new Date(d.fetched_at).toLocaleTimeString()}`, t);
    const body = d && h_flex().gap(10)
      .child(this.tile("REJECTIONS PER MERGED PR", d.rejections_per_pr == null ? "–" : d.rejections_per_pr.toFixed(2), t))
      .child(this.tile("FIRST-TRY MERGE RATE", this.fmtPct(d.first_try_rate), t))
      .child(this.tile("REJECTIONS", String(d.rejections), t))
      .child(this.tile("FIRST-TRY / PRS", `${d.first_try} / ${d.prs}`, t));
    return this.card(d ? `Review quality · ${this.monthName(d)}` : "Review quality", sub, body, t);
  }

  agentsCard(t) {
    const d = this.data.agents;
    const sub = this.sub("agents", (d) => `Claude Code sessions with ≥1 prompt and ≥3 tool calls · cost = ccusage estimate · updated ${new Date(d.fetched_at).toLocaleTimeString()}`, t);
    const body = d && h_flex().gap(10)
      .child(this.tile("COST PER TASK (AVG)", this.usd(d.cost_avg), t))
      .child(this.tile("MEDIAN", this.usd(d.cost_median), t))
      .child(this.tile("P90", this.usd(d.cost_p90), t))
      .child(this.tile("TASKS", String(d.tasks), t))
      .child(this.tile("TOTAL COST", this.usd(d.cost_total), t));
    return this.card(d ? `Agent tasks · ${this.monthName(d)}` : "Agent tasks", sub, body, t);
  }

  // Tasks run by the agentic framework (ale): one row per ticket, builder and reviewer apart. Numbers come from `ale cost`.
  aleCard(t) {
    const d = this.data.ale;
    const sub = this.sub("ale", (d) => `builder + reviewer sessions per ticket · prices = ccusage · rounds = review requests · code = lines changed against the base, generated files left out · updated ${new Date(d.fetched_at).toLocaleTimeString()}`, t);
    if (!d) return this.card("Ale tasks", sub, null, t);
    const rows = d.rows;
    const sum = (f) => rows.reduce((a, r) => a + (f(r) || 0), 0);
    const reviewer = sum((r) => r.reviewer?.cost), total = sum((r) => r.total);
    const share = total ? `${Math.round((reviewer / total) * 100)}%` : "–";
    const rounds = rows.length ? (sum((r) => r.rounds) / rows.length).toFixed(1) : "–";
    const num = (id) => id.replace(/^[A-Z]+-/, "");
    const side = (x, n, unit) => x ? `${this.usd(x.cost)} · ${n} ${unit}` : "–";
    const cell = (child, w, extra) => div().w(w).flex_none().text_size(12).text_color(t.foreground).child(child);
    const head = h_flex().gap(8).pb(4).border_b(1).border_color(t.border)
      .children([["TASK", 88], ["TOTAL", 70], ["BUILDER", 150], ["REVIEWER", 170], ["ROUNDS", 60], ["CODE + / −", 100], ["FILES", 50], ["START CTX B / R", 110], ["REBUILDS B / R", 100]]
        .map(([l, w]) => div().w(w).flex_none().text_size(10).text_color(t.muted).child(l)));
    const body = v_flex().gap(10)
      .child(h_flex().gap(10)
        .child(this.tile("TASKS", String(d.n), t))
        .child(this.tile("TOTAL", this.usd(d.total), t))
        .child(this.tile("AVG PER TASK", this.usd(d.avg), t))
        .child(this.tile("MEDIAN", this.usd(d.median), t))
        .child(this.tile("REVIEWER SHARE", share, t))
        .child(this.tile("AVG ROUNDS", rounds, t)))
      .child(this.columns(rows.map((r) => r.total), rows.map((r) => num(r.task)), { plotH: 90, id: "ale-cost", fmt: (n) => `$${Math.round(n)}` }, t))
      .child(v_flex().id("ale-rows").gap(6).child(head)
        .children(rows.map((r) => h_flex().id(`ale-${r.task}`).gap(8).items_center()
          .child(cell(r.task, 88).font_semibold())
          .child(cell(`${this.usd(r.total)}${r.priced ? "" : "+"}`, 70))
          .child(cell(side(r.builder, r.builder?.turns, "turns"), 150))
          .child(cell(side(r.reviewer, r.reviewer?.turns, "turns"), 170))
          .child(cell(String(r.rounds), 60))
          .child(cell(r.code ? `+${r.code.added} / −${r.code.deleted}` : "–", 100))
          .child(cell(r.code ? String(r.code.files) : "–", 50))
          .child(cell(`${r.builder ? r.builder.start_k + "k" : "–"} / ${r.reviewer ? r.reviewer.start_k + "k" : "–"}`, 110))
          .child(cell(`${r.builder ? r.builder.rebuilds : "–"} / ${r.reviewer ? r.reviewer.rebuilds : "–"}`, 100)))));
    return this.card(`Ale tasks · ${d.n} tickets`, sub, body, t);
  }

  monthName(d) { return `${MONTHS[d.month - 1]} ${d.year}`; }

  cycleCard(t) {
    const d = this.data.cycle;
    const sub = this.sub("cycle", (d) => `first commit in the PR → merge · my ${d.first_commit.n} PRs merged in ${this.monthName(d)} · updated ${new Date(d.fetched_at).toLocaleTimeString()}`, t);
    return this.card(d ? `Cycle time · ${this.monthName(d)}` : "Cycle time", sub, d && this.metrics(d.first_commit, t), t);
  }

  spawnCard(t) {
    const d = this.data.cycle;
    const sub = this.sub("cycle", (d) => d.spawn.n
      ? `herdr terminal spawn → PR merge · matched by ticket key or branch · tracking since ${d.spawn.tracking_since.slice(0, 10)}`
      : `no PR merged in ${this.monthName(d)} matched a spawned terminal yet · ${d.spawn.terminals_logged} terminals logged since ${(d.spawn.tracking_since ?? "").slice(0, 10)} · matched by ticket key or branch`, t);
    return this.card(d ? `Spawn → merge · ${this.monthName(d)}` : "Spawn → merge", sub, d && this.metrics(d.spawn, t), t);
  }

  render() {
    const t = theme();
    return v_flex().size_full().bg(t.canvas).p(16).gap(12)
      .child(div().text_size(18).font_semibold().text_color(t.foreground).child("Stats"))
      .child(v_flex().id("cards").flex_1().min_h(0).gap(12).overflow_y_scrollbar()
        .child(this.aleCard(t))
        .child(this.cycleCard(t))
        .child(this.qualityCard(t))
        .child(this.agentsCard(t))
        .child(this.mergesCard(t))
        .child(this.locCard(t))
        .child(this.spawnCard(t)));
  }
}
