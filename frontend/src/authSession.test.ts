import { describe, expect, it, vi } from "vitest";
import { readAuthStatus, sameOriginRequest } from "./authSession";

describe("operator session helpers", () => {
  it("uses same-origin credentials for status restoration", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      authenticated: true, token_required: true,
    }), { status: 200 }));

    await expect(readAuthStatus(fetcher)).resolves.toEqual({
      authenticated: true, tokenRequired: true,
    });
    expect(fetcher).toHaveBeenCalledWith("/api/auth/status", {
      credentials: "same-origin",
    });
  });

  it("does not treat an unavailable status endpoint as signed out", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response("down", { status: 503 }));

    await expect(readAuthStatus(fetcher)).resolves.toBeNull();
  });

  it("adds same-origin credentials to protected requests", () => {
    expect(sameOriginRequest({ method: "POST" })).toEqual({
      method: "POST", credentials: "same-origin",
    });
  });
});
