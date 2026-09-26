package main

import (
	"encoding/json"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/gin-gonic/gin"
)

func TestFlappingEnvironmentIsolation(t *testing.T) {
	root := t.TempDir()
	previousRoot := serverWebRoot
	serverWebRoot = root
	t.Cleanup(func() { serverWebRoot = previousRoot })
	write := func(name, content string) {
		t.Helper()
		path := filepath.Join(root, name)
		if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(content), 0600); err != nil {
			t.Fatal(err)
		}
	}
	write("registry.json", `{"default":"a","environments":[{"code":"a","data_root":"a"},{"code":"b","data_root":"b"}]}`)
	write("a/flapping.json", `{"ports":[{"device_id":"switch-a"}],"stale":false}`)
	write("flapping.json", `{"ports":[{"device_id":"must-not-leak"}],"stale":false}`)
	t.Setenv("TOPOLOGY_MODE", "multi")
	t.Setenv("ENV_REGISTRY", filepath.Join(root, "registry.json"))
	t.Setenv("DATA_ROOT", filepath.Join(root, "a"))
	router := gin.New()
	router.GET("/topology/flapping.json", flappingHandler)
	request := func(query string) *httptest.ResponseRecorder {
		t.Helper()
		response := httptest.NewRecorder()
		router.ServeHTTP(response, httptest.NewRequest("GET", "/topology/flapping.json"+query, nil))
		return response
	}
	if response := request("?cs=a"); response.Code != 200 || !strings.Contains(response.Body.String(), "switch-a") || !strings.Contains(response.Header().Get("Cache-Control"), "no-store") {
		t.Fatalf("wrong environment or caching: %v", response)
	}
	response := request("?cs=b")
	var missing struct {
		Ports []any `json:"ports"`
		Stale bool  `json:"stale"`
	}
	if err := json.Unmarshal(response.Body.Bytes(), &missing); err != nil || !missing.Stale || len(missing.Ports) != 0 {
		t.Fatalf("missing environment must be unknown, not another environment: %s", response.Body.String())
	}
	if request("?cs=unknown").Code != 400 {
		t.Fatal("unknown environment must be rejected")
	}
	t.Setenv("TOPOLOGY_MODE", "single")
	if response := request(""); response.Code != 200 || !strings.Contains(response.Body.String(), "switch-a") {
		t.Fatal("single mode must use DATA_ROOT without cs")
	}
}
