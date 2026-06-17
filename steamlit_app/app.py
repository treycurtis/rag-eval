"""
app.py — Turkle Eval Dashboard

Time-based quality monitoring for Turkle's conversation outcome classifier.
Reframed from a static all-hands snapshot into a recurring monitoring tool —
see methodology §14 (V2 roadmap) for the three questions this is built to answer:
  1. Is Turkle getting better?
  2. Is it getting more expensive?
  3. Are we seeing weird outliers?
"""

import pandas as pd
import plotly.express as px
import streamlit as st

from data_loader import (
    GATE_ALLOW,
    GATE_BLOCK,
    GATE_REVIEW,
    RUBRIC_DIMENSIONS,
    add_derived_columns,
    has_cost_columns,
    load_outcomes,
    resolve_time_axis,
)

st.set_page_config(page_title="Turkle Eval Dashboard", layout="wide")

df = add_derived_columns(load_outcomes())
time_col, time_is_fallback = resolve_time_axis(df)
has_cost = has_cost_columns(df)

# ── Sidebar navigation ──────────────────────────────────────────────────────
st.sidebar.title("Turkle Quality Signal")
page = st.sidebar.radio(
    "View",
    ["Overview", "Trends", "Outliers & Drift", "Gate Simulator", "Conversation Explorer"],
)
st.sidebar.divider()
st.sidebar.caption(f"{len(df)} classified conversations")
st.sidebar.caption("Source: RAG_EVAL.MARTS.FCT_CONVERSATION_OUTCOMES")

if time_is_fallback:
    st.sidebar.warning(
        "⚠️ Trend panels are using `classified_at` (batch-run timestamps), "
        "not conversation time. Join `created_at` from STG_CONVERSATIONS "
        "into the fact table for accurate trends.",
        icon="⚠️",
    )
if not has_cost:
    st.sidebar.warning(
        "⚠️ Cost columns (total_cost_usd etc.) not found on the fact table. "
        "Cost panels are hidden until int_conversation_metrics is joined in.",
        icon="⚠️",
    )


# ════════════════════════════════════════════════════════════════════════════
# OVERVIEW
# ════════════════════════════════════════════════════════════════════════════
if page == "Overview":
    st.title("Overview")
    st.caption("Current health of the classified corpus.")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Classified conversations", len(df))
    col2.metric("Success rate", f"{(df['outcome_tier'] == 'success').mean():.0%}")
    col3.metric("Failure rate", f"{(df['outcome_tier'] == 'failure').mean():.0%}")
    col4.metric("Inconclusive", f"{(df['outcome_tier'] == 'inconclusive').mean():.0%}")

    st.divider()

    st.subheader("Outcome Distribution")
    left, right = st.columns(2)

    with left:
        type_counts = df.groupby(["conversation_type", "outcome"]).size().reset_index(name="count")
        fig = px.bar(type_counts, x="conversation_type", y="count", color="outcome", barmode="stack")
        st.plotly_chart(fig, use_container_width=True)

    with right:
        tier_counts = df["outcome_tier"].value_counts().reset_index()
        tier_counts.columns = ["tier", "count"]
        fig = px.pie(tier_counts, names="tier", values="count", hole=0.4)
        st.plotly_chart(fig, use_container_width=True)

    st.divider()

    st.subheader("Rubric Score Breakdown")
    rubric_avg = df[RUBRIC_DIMENSIONS].mean().reset_index()
    rubric_avg.columns = ["dimension", "avg_score"]
    fig = px.bar(rubric_avg, x="dimension", y="avg_score", range_y=[0, 3])
    st.plotly_chart(fig, use_container_width=True)

    st.divider()

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Dev Environment Acknowledgment")
        sql_df = df[df["conversation_type"].isin(["generation", "modification"])]
        ack_rate = sql_df["flag_dev_acknowledged"].mean()
        st.metric("flag_dev_acknowledged rate (SQL output)", f"{ack_rate:.0%}")
        st.caption(
            "Per methodology §7: this is a *good* signal when high — Turkle "
            "proactively flagging dev-environment limitations is correct behavior, "
            "not a failure indicator."
        )

    with col2:
        # The §2 anomalous-cost callout, made permanent rather than a one-off slide.
        st.subheader("Cost Concentration")
        if has_cost:
            type_cost = (
                df.groupby("conversation_type")["total_cost_usd"]
                .agg(["sum", "count"])
                .reset_index()
            )
            type_cost.columns = ["conversation_type", "total_cost", "count"]
            total_cost_all = type_cost["total_cost"].sum()
            total_count_all = type_cost["count"].sum()
            type_cost["pct_of_cost"] = type_cost["total_cost"] / total_cost_all
            type_cost["pct_of_count"] = type_cost["count"] / total_count_all

            # Highlight whichever type has the largest cost/count imbalance.
            type_cost["imbalance"] = type_cost["pct_of_cost"] - type_cost["pct_of_count"]
            worst = type_cost.sort_values("imbalance", ascending=False).iloc[0]

            st.metric(
                f"{worst['conversation_type']} cost concentration",
                f"{worst['pct_of_cost']:.0%} of cost",
                f"{worst['pct_of_count']:.0%} of conversations",
            )
            st.dataframe(
                type_cost[["conversation_type", "count", "total_cost", "pct_of_cost"]]
                .sort_values("total_cost", ascending=False)
                .style.format({"total_cost": "${:.2f}", "pct_of_cost": "{:.0%}"}),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("Cost columns not available — see sidebar warning.")


# ════════════════════════════════════════════════════════════════════════════
# TRENDS
# ════════════════════════════════════════════════════════════════════════════
elif page == "Trends":
    st.title("Trends Over Time")
    st.caption("Is Turkle getting better? Is it getting more expensive?")

    if time_col is None:
        st.error(
            "No usable time column found on FCT_CONVERSATION_OUTCOMES "
            "(checked created_at, conversation_created_at, classified_at). "
            "Trend panels can't be built until one of these exists."
        )
    else:
        if time_is_fallback:
            st.warning(
                "Using `classified_at` — these conversations were likely classified "
                "in a handful of batch runs, so this axis may show spikes rather "
                "than a smooth trend. Treat as provisional until `created_at` is "
                "joined in from STG_CONVERSATIONS."
            )

        df["_time"] = pd.to_datetime(df[time_col])
        span_days = (df["_time"].max() - df["_time"].min()).days

        granularity = st.radio(
            "Granularity",
            ["Weekly", "Monthly"],
            index=1 if span_days < 120 else 0,
            horizontal=True,
            help="With ~400 conversations total, weekly bins can get sparse — "
                 "monthly is the safer default for a short date range.",
        )
        freq = "W" if granularity == "Weekly" else "M"
        df["_period"] = df["_time"].dt.to_period(freq).dt.start_time

        # Show bin sizes so sparsity is visible rather than hidden.
        bin_counts = df.groupby("_period").size()
        if (bin_counts < 5).any():
            st.caption(
                f"⚠️ Some {granularity.lower()} bins have fewer than 5 conversations "
                "— trend lines for those periods are noisy. Hover points show n."
            )

        st.subheader("Outcome Mix Over Time")
        period_outcomes = (
            df.groupby(["_period", "outcome_tier"]).size().reset_index(name="count")
        )
        period_totals = period_outcomes.groupby("_period")["count"].sum().reset_index(name="total")
        period_outcomes = period_outcomes.merge(period_totals, on="_period")
        period_outcomes["share"] = period_outcomes["count"] / period_outcomes["total"]

        fig = px.area(
            period_outcomes,
            x="_period",
            y="share",
            color="outcome_tier",
            groupnorm="fraction",
        )
        fig.update_layout(yaxis_tickformat=".0%", xaxis_title="Period", yaxis_title="Share")
        st.plotly_chart(fig, use_container_width=True)

        st.subheader("Rubric Scores Over Time")
        period_rubric = df.groupby("_period")[RUBRIC_DIMENSIONS].mean().reset_index()
        period_n = df.groupby("_period").size().reset_index(name="n")
        period_rubric = period_rubric.merge(period_n, on="_period")
        rubric_melted = period_rubric.melt(
            id_vars=["_period", "n"], value_vars=RUBRIC_DIMENSIONS,
            var_name="dimension", value_name="avg_score"
        )
        fig = px.line(
            rubric_melted, x="_period", y="avg_score", color="dimension",
            markers=True, range_y=[1, 3],
            hover_data=["n"],
        )
        st.plotly_chart(fig, use_container_width=True)

        if has_cost:
            st.subheader("Cost Over Time")
            st.caption(
                "Anomalous-type conversations are shown separately — they "
                "dominate cost (methodology §2) and would otherwise mask "
                "the trend for normal working conversations."
            )

            cost_df = df.copy()
            cost_df["bucket"] = cost_df["conversation_type"].apply(
                lambda t: "anomalous" if t == "anomalous" else "normal"
            )
            period_cost = (
                cost_df.groupby(["_period", "bucket"])["total_cost_usd"]
                .agg(["sum", "mean", "count"])
                .reset_index()
            )

            tab1, tab2 = st.tabs(["Total cost", "Avg cost / conversation"])
            with tab1:
                fig = px.bar(
                    period_cost, x="_period", y="sum", color="bucket", barmode="group"
                )
                fig.update_layout(yaxis_title="Total cost (USD)")
                st.plotly_chart(fig, use_container_width=True)
            with tab2:
                fig = px.line(
                    period_cost, x="_period", y="mean", color="bucket", markers=True,
                    hover_data=["count"],
                )
                fig.update_layout(yaxis_title="Avg cost per conversation (USD)")
                st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Cost columns not available — see sidebar warning.")


# ════════════════════════════════════════════════════════════════════════════
# OUTLIERS & DRIFT
# ════════════════════════════════════════════════════════════════════════════
elif page == "Outliers & Drift":
    st.title("Outliers & Drift Watch")
    st.caption("Are we seeing weird stuff? Cost spikes, failure-rate jumps, distribution shifts.")

    st.subheader("Cost Outliers")
    if has_cost:
        cost_series = df["total_cost_usd"].dropna()
        mean_cost = cost_series.mean()
        std_cost = cost_series.std()
        threshold = mean_cost + 2 * std_cost

        outliers = df[df["total_cost_usd"] > threshold].sort_values(
            "total_cost_usd", ascending=False
        )

        col1, col2, col3 = st.columns(3)
        col1.metric("Mean cost / conversation", f"${mean_cost:.2f}")
        col2.metric("2σ threshold", f"${threshold:.2f}")
        col3.metric("Conversations above threshold", len(outliers))

        if len(outliers):
            st.caption(
                "Conversations costing more than mean + 2 standard deviations. "
                "Per methodology §2, a high cost alone isn't necessarily a problem "
                "(conv 599 was a legitimate long session) — but it's worth a look."
            )
            display_cols = ["conversation_id", "conversation_type", "outcome", "total_cost_usd"]
            if "run_count" in df.columns:
                display_cols.append("run_count")
            st.dataframe(
                outliers[display_cols].style.format({"total_cost_usd": "${:.2f}"}),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.success("No conversations above the 2σ cost threshold.")
    else:
        st.info("Cost columns not available — see sidebar warning.")

    st.divider()

    st.subheader("Outcome Distribution Drift")
    if time_col is None:
        st.info("No time column available — drift detection needs a time axis. See Trends page.")
    else:
        st.caption(
            "Compares the most recent period against the corpus-wide baseline. "
            "A large shift in any outcome's share is worth investigating — it "
            "could mean Turkle got better/worse, or that the recent sample "
            "is just small (check n before reacting)."
        )

        df["_time"] = pd.to_datetime(df[time_col])
        df_sorted = df.sort_values("_time")

        n_recent = st.slider(
            "Size of 'recent' window (most recent N conversations)",
            min_value=10, max_value=min(100, len(df)), value=min(30, len(df)),
        )

        recent = df_sorted.tail(n_recent)
        baseline = df_sorted

        recent_dist = recent["outcome_tier"].value_counts(normalize=True)
        baseline_dist = baseline["outcome_tier"].value_counts(normalize=True)

        drift = pd.DataFrame({
            "baseline_share": baseline_dist,
            "recent_share": recent_dist,
        }).fillna(0.0)
        drift["delta"] = drift["recent_share"] - drift["baseline_share"]

        fig = px.bar(
            drift.reset_index().rename(columns={"index": "outcome_tier"}),
            x="outcome_tier", y="delta",
            color="delta",
            color_continuous_scale=["red", "lightgray", "green"],
            range_color=[-0.3, 0.3],
        )
        fig.update_layout(yaxis_tickformat=".0%", yaxis_title="Recent − baseline share")
        st.plotly_chart(fig, use_container_width=True)
        st.caption(f"'Recent' = last {n_recent} conversations by {time_col}.")


# ════════════════════════════════════════════════════════════════════════════
# GATE SIMULATOR
# ════════════════════════════════════════════════════════════════════════════
elif page == "Gate Simulator":
    st.title("Auto-Learning Gate Simulator")
    st.caption(
        "Simulates what the proposed quality gate would "
        "allow or block, applied to the current corpus. Not yet wired into "
        "the auto-learning pipeline; this is a sanity check on real data."
    )

    gate_counts = df["gate_decision"].value_counts().reindex(
        ["allow", "block_review", "block"], fill_value=0
    )

    col1, col2, col3 = st.columns(3)
    col1.metric("Allow (writes proceed)", gate_counts["allow"],
                 help=f"Outcomes: {', '.join(sorted(GATE_ALLOW))}")
    col2.metric("Block + human review", gate_counts["block_review"],
                 help=f"Outcomes: {', '.join(sorted(GATE_REVIEW))}")
    col3.metric("Block (no review)", gate_counts["block"],
                 help=f"Outcomes: {', '.join(sorted(GATE_BLOCK - GATE_REVIEW))}")

    st.divider()

    st.subheader("Gate Decision by Conversation Type")
    type_gate = df.groupby(["conversation_type", "gate_decision"]).size().reset_index(name="count")
    fig = px.bar(
        type_gate, x="conversation_type", y="count", color="gate_decision",
        barmode="stack",
        category_orders={"gate_decision": ["allow", "block_review", "block"]},
        color_discrete_map={"allow": "#1aa125", "block_review": "#ff7f0e", "block": "#d62728"},
    )
    st.plotly_chart(fig, use_container_width=True)

    st.divider()

    st.subheader("What the gate would block — :red[failure_wrong_direction] review queue")
    st.caption(
        "This is the highest-risk case: Turkle was confidently "
        "wrong and never self-corrected. These are the conversations where "
        "the extraction pipeline would otherwise write a confidently-wrong "
        "learning into shared memory or table documentation."
    )
    review_queue = df[df["outcome"] == "failure_wrong_direction"]
    if len(review_queue):
        st.dataframe(
            review_queue[["conversation_id", "conversation_type", "reasoning"]],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No failure_wrong_direction conversations in the current corpus.")


# ════════════════════════════════════════════════════════════════════════════
# CONVERSATION EXPLORER
# ════════════════════════════════════════════════════════════════════════════
elif page == "Conversation Explorer":
    st.title("Conversation Explorer")
    st.caption("Filterable view for spot-checking and Q&A during review.")

    id_search = st.text_input(
        "Search by conversation_id",
        placeholder="e.g. 599 — comma-separate for multiple",
        help="Filters to matching conversation_id(s). Leave blank to show all.",
    )

    col1, col2 = st.columns(2)
    with col1:
        outcome_filter = st.multiselect(
            "Filter by outcome", sorted(df["outcome"].unique()),
            default=sorted(df["outcome"].unique())
        )
    with col2:
        type_filter = st.multiselect(
            "Filter by conversation type", sorted(df["conversation_type"].unique()),
            default=sorted(df["conversation_type"].unique())
        )

    filtered = df[df["outcome"].isin(outcome_filter) & df["conversation_type"].isin(type_filter)]

    if id_search.strip():
        search_ids = [tok.strip() for tok in id_search.split(",") if tok.strip()]
        id_as_str = filtered["conversation_id"].astype(str)
        filtered = filtered[id_as_str.isin(search_ids)]
        unmatched = [s for s in search_ids if s not in set(id_as_str)]
        if unmatched:
            st.warning(f"No match for conversation_id: {', '.join(unmatched)}")

    st.caption(f"{len(filtered)} of {len(df)} conversations")

    display_cols = ["conversation_id", "conversation_type", "outcome", "gate_decision",
                     "reasoning", "char_count"] + RUBRIC_DIMENSIONS
    if has_cost:
        display_cols.insert(6, "total_cost_usd")

    st.dataframe(
        filtered[[c for c in display_cols if c in filtered.columns]],
        use_container_width=True,
        hide_index=True,
    )