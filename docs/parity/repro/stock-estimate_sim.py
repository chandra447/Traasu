#!/usr/bin/env python3
"""The register's stock estimate: `server value - its unsynced sales` (D27, query-catalogue §3).

Truth = the shelf. `server` = the last value the register pulled. `unsynced` = bills with
synced_at IS NULL (DESIGN-BRIEF: the terminal marks only the accepted ids as synced).
"""
def show(step, local, truth, note=""):
    err = local - truth
    mark = "  <-- WRONG" if err else ""
    print(f"  {step:<44} local {local:>3}   truth {truth:>3}   error {err:>+3}{mark}  {note}")


def formula(server, unsynced_sales, unsynced_voids=0, voids_counted=False):
    """The documented rule, plus the variant that also adds back unsynced voids."""
    v = server - unsynced_sales
    if voids_counted:
        v += unsynced_voids
    return v


def head(t):
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


head("A  Push ack marks the bill synced before the stock pull (the natural implementation)")
server, truth, unsynced = 10, 10, 0
show("start, nothing pending", formula(server, unsynced), truth)
unsynced = 3
truth = 7
show("register sells 3 offline", formula(server, unsynced), truth)
unsynced = 0                      # push acked -> synced_at set; the pull has not happened yet
show("push accepted, unsynced cleared, no pull yet", formula(server, unsynced), truth,
     "3 phantom packs on screen")
server = 7
show("stock pull lands", formula(server, unsynced), truth)

head("B  Pull first, mark synced after (same two calls, the other order)")
server, truth, unsynced = 10, 10, 0
unsynced = 3
truth = 7
show("register sells 3 offline", formula(server, unsynced), truth)
server = 7                        # the pull already contains the sale, the bill is still unsynced
show("push accepted, pull applied, not yet marked", formula(server, unsynced), truth,
     "the same sale subtracted twice")
unsynced = 0
show("bill marked synced", formula(server, unsynced), truth)

head("C  Push ack marks synced in the same local transaction as the stock pull")
server, truth, unsynced = 10, 10, 0
unsynced = 3
truth = 7
show("register sells 3 offline", formula(server, unsynced), truth)
server, unsynced = 7, 0           # one transaction: apply the pull AND clear the accepted bills
show("push accepted + pull applied atomically", formula(server, unsynced), truth)

head("D  A void is not a sale: the documented formula has no void term")
server, truth = 10, 10
unsynced_s, unsynced_v = 2, 0
truth = 8
show("sell 2 offline", formula(server, unsynced_s, unsynced_v), truth)
server, unsynced_s = 8, 0         # the sale pushed
unsynced_v = 2
truth = 10
show("void raised offline (goods back on the shelf)", formula(server, unsynced_s, unsynced_v), truth,
     "2 packs on the shelf, register says 8")
show("  same moment, counting unsynced voids too", formula(server, unsynced_s, unsynced_v, True), truth)
server, unsynced_v = 10, 0
show("void pushed and pulled", formula(server, unsynced_s, unsynced_v), truth)

head("E  A push that succeeds while the stock pull keeps failing (429 / 500)")
server, truth, unsynced = 10, 10, 0
for n in (2, 2, 3):
    truth -= n
    unsynced += n
    show(f"sell {n} offline", formula(server, unsynced), truth)
unsynced = 0                      # pushes acked one by one; every stock pull failed
show("all 7 pushed, every stock pull failed", formula(server, unsynced), truth,
     "the register shows the old server number")
print("\nDone.")
