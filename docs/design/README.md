# Overcode Design Documents

This directory contains technical design documents, architecture analysis, and implementation roadmaps. These are **not user-facing documentation** — see `/docs/` for user guides.

## Documents

### ACP Integration Analysis
- **File:** `acp-agent-integration-analysis.md`
- **Status:** Reference / Investigation
- **Date:** March 2026
- **Summary:** Comprehensive analysis of feasibility for integrating Agent Client Protocol (ACP) for monitoring and controlling Claude Code agents via overcode. Evaluates three approaches (sister protocol only, ACP extensions, hybrid) and recommends a hybrid approach.
- **Audience:** Contributors, architects, anyone considering remote agent monitoring integration
- **Key Finding:** ACP covers ~25-30% of overcode features; recommend hybrid model (sister protocol for control, ACP for observability)

### Model Alias Resolution (proposal)
- **File:** `model-alias-resolution.md`
- **Status:** Proposal / Not implemented
- **Date:** September 2026
- **Summary:** How to map model ids overcode doesn't recognise — internal naming conventions, gateway aliases — onto the bundled models.dev catalog: a deterministic alias file first, then an opt-in LLM-assisted resolver that writes confident matches back to that file. Follow-on to #473.
- **Audience:** Contributors; users behind corporate gateways with their own model names

### opencode stats reader performance (#476)
- **File:** `opencode-stats-reader-performance.md`
- **Status:** Implemented in 0.5.4
- **Date:** September 2026
- **Summary:** Profiling of the opencode/opencode2 stats readers' message scan against a generated 2.3 GB store, why the proposed 60 s TTL cache was declined, and the fix: per-conversation index probes plus a per-row parse cache (opencode2 `get_stats` at eight owned conversations 254 ms → 9.6 ms).
- **Audience:** Contributors touching the backend stats readers or the TUI's polling

---

## Contributing Design Docs

When adding new design documentation:

1. **Use clear structure:**
   - Executive summary at top
   - Background/context
   - Analysis/findings
   - Recommendations with effort estimates
   - Open questions
   - References

2. **Document assumptions and constraints**
   - Why are we considering this?
   - What are the non-negotiables?
   - What's the timeline?

3. **Include comparisons**
   - Matrix tables showing trade-offs
   - Effort estimates (in weeks/months)
   - Risk assessment

4. **Add to this README**
   - Brief summary
   - Link to document
   - Date and status

5. **Keep user docs separate**
   - User guides → `/docs/`
   - Design docs → `/docs/design/`

---

## Related Documentation

- **User Guides:** See `/docs/` for getting started, configuration, CLI reference
- **Architecture Overview:** `/docs/architecture.md` (high-level system design)
- **Performance Analysis:** `/docs/ux-performance-analysis-2026-03-09.md`

---

**Last updated:** March 19, 2026
