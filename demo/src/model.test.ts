import { describe, expect, it } from "vitest";
import { initialState, latest, reducer } from "./model";

const id = "demo-visitor-01";
describe("isolated demo behavior", () => {
  it("blocks sends while offline without queuing a later replay", () => {
    const original = initialState();
    const offline = reducer(original, { type: "toggleOnline" });
    const sent = reducer(offline, {
      type: "send",
      id,
      text: "should not send",
    });
    expect(sent).toBe(offline);
    expect(reducer(sent, { type: "toggleOnline" }).conversations).toEqual(
      original.conversations,
    );
  });
  it("preserves live watermarks, unread counts and manual settings during recovery", () => {
    const original = initialState();
    const recovering = reducer(original, { type: "beginRecovery" });
    recovering.conversations.forEach((c, index) => {
      expect(latest(c)).toEqual(latest(original.conversations[index]));
      expect(c.ai).toBe(original.conversations[index].ai);
      expect(c.unread).toBe(original.conversations[index].unread);
    });
    expect(reducer(recovering, { type: "beginRecovery" })).toBe(recovering);
    const ended = reducer(recovering, { type: "endRecovery" });
    expect(reducer(ended, { type: "beginRecovery" }).conversations).toEqual(
      recovering.conversations,
    );
  });
  it("keeps observations during recovery history-only, then replies to truly new live input", () => {
    const recovering = reducer(initialState(), { type: "beginRecovery" });
    const observed = reducer(recovering, {
      type: "receive",
      id,
      text: "old spool message",
    });
    expect(observed.conversations[0].messages.at(-1)?.history).toBe(true);
    expect(latest(observed.conversations[0])).toEqual(
      latest(recovering.conversations[0]),
    );
    expect(reducer(observed, { type: "send", id, text: "blocked" })).toBe(
      observed,
    );
    const ended = reducer(observed, { type: "endRecovery" });
    expect(ended.conversations).toEqual(observed.conversations);
    const live = reducer(ended, { type: "receive", id, text: "新的连接问题" });
    expect(
      live.conversations[0].messages.slice(-2).map((m) => m.direction),
    ).toEqual(["inbound", "outbound"]);
    expect(live.conversations[0].messages.at(-1)?.author).toBe("ai");
  });
  it("honors global AI and conversation takeover independently", () => {
    for (const paused of [
      reducer(initialState(), { type: "toggleGlobalAi" }),
      reducer(initialState(), { type: "toggleAi", id }),
    ]) {
      const received = reducer(paused, { type: "receive", id, text: "你好" });
      expect(received.conversations[0].messages).toHaveLength(
        paused.conversations[0].messages.length + 1,
      );
      expect(received.conversations[0].messages.at(-1)?.direction).toBe(
        "inbound",
      );
    }
  });
  it("rejects whitespace and bounds user text", () => {
    const original = initialState();
    expect(reducer(original, { type: "send", id, text: "  " })).toBe(original);
    expect(
      reducer(original, {
        type: "send",
        id,
        text: "x".repeat(2100),
      }).conversations[0].messages.at(-1)?.text,
    ).toHaveLength(2000);
  });
  it("resets all ephemeral state", () => {
    const changed = reducer(
      reducer(initialState(), { type: "beginRecovery" }),
      { type: "toggleOnline" },
    );
    expect(reducer(changed, { type: "reset" })).toEqual(initialState());
  });
});
