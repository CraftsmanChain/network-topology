package main

import "testing"

func hasLink(links []Link, want Link) bool {
	for _, link := range links {
		if undirectedLinkKey(link) == undirectedLinkKey(want) {
			return true
		}
	}
	return false
}

func TestResolvePortConflictsPrefersHigherPairSupport(t *testing.T) {
	preferred := Link{
		Source:     "NXDXB01F02-A1-1-OOB-AGG2",
		SourcePort: "GigabitEthernet1/0/2",
		Target:     "NXDXB01F01-A1-3-OOB-28",
		TargetPort: "XGigabitEthernet0/0/1",
	}
	conflict := Link{
		Source:     "NXDXB01F02-A1-1-OOB-AGG2",
		SourcePort: "GigabitEthernet1/0/2",
		Target:     "NXDXB01F01-A1-3-OOB-28",
		TargetPort: "GigabitEthernet 0/0/44",
	}

	resolved := resolvePortConflicts([]Link{preferred, conflict}, map[string]int{
		undirectedLinkKey(preferred): 2,
		undirectedLinkKey(conflict):  1,
	})

	if len(resolved) != 1 {
		t.Fatalf("expected exactly one link after conflict resolution, got %d: %+v", len(resolved), resolved)
	}
	if !hasLink(resolved, preferred) {
		t.Fatalf("expected preferred link to remain, got %+v", resolved)
	}
	if hasLink(resolved, conflict) {
		t.Fatalf("expected conflicting link to be removed, got %+v", resolved)
	}
}

func TestResolvePortConflictsUsesRemoteConflictCountAsTieBreaker(t *testing.T) {
	preferred := Link{
		Source:     "leaf-a",
		SourcePort: "Eth1/1",
		Target:     "spine-a",
		TargetPort: "Eth1/49",
	}
	conflict := Link{
		Source:     "leaf-a",
		SourcePort: "Eth1/1",
		Target:     "spine-b",
		TargetPort: "Eth1/49",
	}
	remoteConflict := Link{
		Source:     "spine-b",
		SourcePort: "Eth1/49",
		Target:     "leaf-b",
		TargetPort: "Eth1/1",
	}

	resolved := resolvePortConflicts([]Link{preferred, conflict, remoteConflict}, map[string]int{
		undirectedLinkKey(preferred):      1,
		undirectedLinkKey(conflict):       1,
		undirectedLinkKey(remoteConflict): 1,
	})

	if len(resolved) != 2 {
		t.Fatalf("expected two links after resolving shared-port conflict, got %d: %+v", len(resolved), resolved)
	}
	if !hasLink(resolved, preferred) {
		t.Fatalf("expected lower-conflict remote endpoint to remain, got %+v", resolved)
	}
	if hasLink(resolved, conflict) {
		t.Fatalf("expected higher-conflict remote endpoint to be removed, got %+v", resolved)
	}
	if !hasLink(resolved, remoteConflict) {
		t.Fatalf("expected unrelated remote conflict link to remain, got %+v", resolved)
	}
}
