package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/gin-contrib/gzip"
	"github.com/gin-gonic/gin"
	jwt "github.com/golang-jwt/jwt/v5"
)

// Minimal backend for config editing with auth from config/.users.local

const releaseVersion = "v2.0.0"

func main() {
	r := gin.Default()
	r.Use(CORSMiddleware())
	// gzip compression to reduce payload size of JSON/HTML
	r.Use(gzip.Gzip(gzip.DefaultCompression))
	// Topology HTML/JSON changes frequently and must not be cached aggressively,
	// otherwise 8281 测试环境很容易看到旧页面和旧配置。
	r.Use(func(c *gin.Context) {
		p := c.Request.URL.Path
		// Long cache for static assets (icons/images)
		if strings.HasPrefix(p, "/topology/icons/") ||
			strings.HasSuffix(p, ".png") || strings.HasSuffix(p, ".jpg") || strings.HasSuffix(p, ".jpeg") || strings.HasSuffix(p, ".svg") {
			c.Header("Cache-Control", "public, max-age=31536000, immutable")
		} else if p == "/topology" || p == "/topology/" || strings.HasSuffix(p, ".html") || strings.HasSuffix(p, ".json") {
			setNoCacheHeaders(c)
		} else {
			c.Header("Cache-Control", "public, max-age=300")
		}
		c.Next()
	})

	// All routes grouped under /topology
	topo := r.Group("/topology")

	// health
	topo.GET("/api/health", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{"status": "ok", "version": releaseVersion})
	})

	// login
	topo.POST("/api/login", loginHandler)

	// config read endpoints
	topo.GET("/api/config/missing", missingHandler)
	topo.GET("/api/config/:name", getConfigHandler)

	// protected endpoints
	secret := getJWTSecret()
	api := topo.Group("/api", AuthMiddleware([]byte(secret)))
	api.GET("/auth/check", func(c *gin.Context) { c.JSON(http.StatusOK, gin.H{"status": "ok"}) })
	api.PUT("/config/:name", putConfigHandler)
	api.GET("/history/:name", listHistoryHandler)
	api.POST("/history/:name/rollback", rollbackHistoryHandler)
	api.POST("/history/:name/backup", backupHistoryHandler)

	// static web preview
	webRoot := os.Getenv("WEB_ROOT")
	if webRoot == "" {
		webRoot = ".."
	}
	dataRoot := getDataRoot(webRoot)
	configDir := getConfigDir()
	// serve all files under web root (../) under /topology/web
	topo.Static("/web", webRoot)
	// serve icons under /topology/icons to match front-end relative paths
	topo.Static("/icons", filepath.Join(webRoot, "icons"))

	// 所有入口统一落到架构视图；管理后台仍保留用于编辑架构相关配置。
	topo.GET("/zg-debug.html", func(c *gin.Context) {
		serveNoCacheFile(c, filepath.Join(webRoot, "topology.html"))
	})
	topo.GET("/zp-debug.html", func(c *gin.Context) {
		serveNoCacheFile(c, filepath.Join(webRoot, "topology.html"))
	})
	topo.GET("/login.html", func(c *gin.Context) { serveNoCacheFile(c, filepath.Join(webRoot, "login.html")) })
	topo.GET("/config.html", func(c *gin.Context) { serveNoCacheFile(c, filepath.Join(webRoot, "config.html")) })
	topo.GET("/", func(c *gin.Context) { serveNoCacheFile(c, filepath.Join(webRoot, "topology.html")) })
	topo.GET("/topology.html", func(c *gin.Context) { serveNoCacheFile(c, filepath.Join(webRoot, "topology.html")) })
	topo.GET("/classic.html", func(c *gin.Context) { serveNoCacheFile(c, filepath.Join(webRoot, "topology.html")) })
	topo.GET("/modern.html", func(c *gin.Context) { serveNoCacheFile(c, filepath.Join(webRoot, "topology.html")) })
	topo.GET("/topology-modern.html", func(c *gin.Context) { serveNoCacheFile(c, filepath.Join(webRoot, "topology.html")) })

	// expose active config directory under /topology/config so frontends can fetch JSON directly
	topo.Static("/config", configDir)

	// provide links.json and prometheus.json if present; fallback to empty JSON
	topo.GET("/links.json", func(c *gin.Context) {
		p := filepath.Join(dataRoot, "links.json")
		if _, err := os.Stat(p); err == nil {
			c.File(p)
			return
		}
		p = filepath.Join(webRoot, "links.json")
		if _, err := os.Stat(p); err == nil {
			c.File(p)
			return
		}
		c.Data(http.StatusOK, "application/json", []byte("{}"))
	})
	topo.GET("/prometheus.json", func(c *gin.Context) {
		p := filepath.Join(dataRoot, "prometheus.json")
		if _, err := os.Stat(p); err == nil {
			c.File(p)
			return
		}
		p = filepath.Join(webRoot, "prometheus.json")
		if _, err := os.Stat(p); err == nil {
			c.File(p)
			return
		}
		c.Data(http.StatusOK, "application/json", []byte("{\"lines\":[],\"last_updated\":null}"))
	})

	// devices.json: serve if present at root or under config; fallback to empty list
	topo.GET("/devices.json", func(c *gin.Context) {
		candidates := []string{
			filepath.Join(dataRoot, "devices.json"),
			filepath.Join(dataRoot, "config", "devices.json"),
			filepath.Join(webRoot, "devices.json"),
			filepath.Join(webRoot, "config", "devices.json"),
		}
		for _, p := range candidates {
			if _, err := os.Stat(p); err == nil {
				c.File(p)
				return
			}
		}
		c.Data(http.StatusOK, "application/json", []byte("[]"))
	})

	// topology aliases for legacy paths
	topo.GET("/topology_config.json", func(c *gin.Context) {
		p := filepath.Join(configDir, "topology_config.json")
		if _, err := os.Stat(p); err == nil {
			c.File(p)
			return
		}
		p = filepath.Join(webRoot, "config", "topology_config.json")
		if _, err := os.Stat(p); err == nil {
			c.File(p)
			return
		}
		c.Data(http.StatusOK, "application/json", []byte("{}"))
	})
	topo.GET("/topology.config.json", func(c *gin.Context) {
		p := filepath.Join(configDir, "topology.config.json")
		if _, err := os.Stat(p); err == nil {
			c.File(p)
			return
		}
		p = filepath.Join(webRoot, "config", "topology.config.json")
		if _, err := os.Stat(p); err == nil {
			c.File(p)
			return
		}
		// fallback to topology_config.json
		p2 := filepath.Join(configDir, "topology_config.json")
		if _, err := os.Stat(p2); err == nil {
			c.File(p2)
			return
		}
		p2 = filepath.Join(webRoot, "config", "topology_config.json")
		if _, err := os.Stat(p2); err == nil {
			c.File(p2)
			return
		}
		c.Data(http.StatusOK, "application/json", []byte("{}"))
	})
	topo.GET("/topology.json", func(c *gin.Context) {
		// Prefer real topology data if present; fallback to debug; then empty graph
		candidates := []string{
			filepath.Join(dataRoot, "topology.json"),
			filepath.Join(dataRoot, "topology-debug.json"),
			filepath.Join(webRoot, "topology.json"),
			filepath.Join(webRoot, "topology-debug.json"),
		}
		for _, p := range candidates {
			if _, err := os.Stat(p); err == nil {
				c.File(p)
				return
			}
		}
		c.Data(http.StatusOK, "application/json", []byte("{\"nodes\":[],\"edges\":[]}"))
	})
	topo.GET("/topology-debug.json", func(c *gin.Context) {
		// try file at project root; fallback to empty nodes/edges
		p := filepath.Join(webRoot, "topology-debug.json")
		if _, err := os.Stat(p); err == nil {
			c.File(p)
			return
		}
		c.Data(http.StatusOK, "application/json", []byte("{\"nodes\":[],\"edges\":[]}"))
	})

	port := os.Getenv("PORT")
	if port == "" {
		port = "8181"
	}
	_ = r.Run(":" + port)
}

func setNoCacheHeaders(c *gin.Context) {
	c.Header("Cache-Control", "no-store, no-cache, must-revalidate")
	c.Header("Pragma", "no-cache")
	c.Header("Expires", "0")
}

func serveNoCacheFile(c *gin.Context, path string) {
	setNoCacheHeaders(c)
	c.File(path)
}

// ===== CORS =====
func CORSMiddleware() gin.HandlerFunc {
	return func(c *gin.Context) {
		c.Writer.Header().Set("Access-Control-Allow-Origin", "*")
		c.Writer.Header().Set("Access-Control-Allow-Credentials", "true")
		c.Writer.Header().Set("Access-Control-Allow-Headers", "Content-Type, Authorization, Content-Length, X-Requested-With")
		c.Writer.Header().Set("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
		if c.Request.Method == http.MethodOptions {
			c.AbortWithStatus(http.StatusNoContent)
			return
		}
		c.Next()
	}
}

// ===== Auth & Login =====
type LoginRequest struct {
	Username string `json:"username"`
	Password string `json:"password"`
}
type LoginResponse struct {
	Token     string `json:"token"`
	ExpiresAt int64  `json:"expires_at"`
}

func getJWTSecret() string {
	s := os.Getenv("JWT_SECRET")
	if s == "" {
		s = "dev-secret-change-me"
	}
	return s
}

func loginHandler(c *gin.Context) {
	var req LoginRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "invalid json"})
		return
	}

	user, pass, err := readUsersLocal()
	if err != nil {
		c.JSON(http.StatusUnauthorized, gin.H{"error": "users.local missing or invalid"})
		return
	}
	if req.Username != user || req.Password != pass {
		c.JSON(http.StatusUnauthorized, gin.H{"error": "invalid credentials"})
		return
	}

	// issue JWT
	expires := time.Now().Add(24 * time.Hour).Unix()
	claims := jwt.MapClaims{
		"sub": req.Username,
		"exp": expires,
		"iat": time.Now().Unix(),
	}
	token := jwt.NewWithClaims(jwt.SigningMethodHS256, claims)
	signed, err := token.SignedString([]byte(getJWTSecret()))
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "token signing failed"})
		return
	}
	c.JSON(http.StatusOK, LoginResponse{Token: signed, ExpiresAt: expires})
}

func AuthMiddleware(secret []byte) gin.HandlerFunc {
	return func(c *gin.Context) {
		auth := c.GetHeader("Authorization")
		if !strings.HasPrefix(auth, "Bearer ") {
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"error": "missing bearer token"})
			return
		}
		tokenStr := strings.TrimPrefix(auth, "Bearer ")
		token, err := jwt.Parse(tokenStr, func(t *jwt.Token) (interface{}, error) {
			if _, ok := t.Method.(*jwt.SigningMethodHMAC); !ok {
				return nil, fmt.Errorf("unexpected signing method")
			}
			return secret, nil
		})
		if err != nil || !token.Valid {
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"error": "invalid token"})
			return
		}
		if claims, ok := token.Claims.(jwt.MapClaims); ok {
			if sub, ok2 := claims["sub"].(string); ok2 {
				c.Set("user", sub)
			}
		}
		c.Next()
	}
}

// ===== Config IO =====
var allowedNames = map[string]string{
	"group_rules":     "group_rules.json",
	"topology_config": "topology_config.json",
	"architecture":    "architecture_config.json",
}

func getConfigDir() string {
	dir := os.Getenv("CONFIG_DIR")
	if dir == "" {
		// default relative to server directory
		dir = filepath.Join("..", "config")
	}
	return dir
}

func getDataRoot(webRoot string) string {
	root := os.Getenv("DATA_ROOT")
	if root == "" {
		return webRoot
	}
	return root
}

func resolveConfigPath(name string) (string, error) {
	file, ok := allowedNames[name]
	if !ok {
		return "", errors.New("unsupported config name")
	}
	return filepath.Join(getConfigDir(), file), nil
}

func getConfigHandler(c *gin.Context) {
	name := c.Param("name")
	path, err := resolveConfigPath(name)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}
	b, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			c.JSON(http.StatusNotFound, gin.H{"error": "not found"})
		} else {
			c.JSON(http.StatusInternalServerError, gin.H{"error": "read error"})
		}
		return
	}
	// return raw JSON
	c.Data(http.StatusOK, "application/json", b)
}

func putConfigHandler(c *gin.Context) {
	name := c.Param("name")
	path, err := resolveConfigPath(name)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}
	body, err := io.ReadAll(c.Request.Body)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "read body failed"})
		return
	}
	if !json.Valid(body) {
		c.JSON(http.StatusBadRequest, gin.H{"error": "invalid json"})
		return
	}
	// Detect no-op update to avoid creating history/backup when content is unchanged
	prev, _ := os.ReadFile(path)
	if len(prev) > 0 && bytes.Equal(prev, body) {
		// Skip backup/history, return ok directly
		c.JSON(http.StatusOK, gin.H{"status": "ok", "message": "no changes"})
		return
	}
	if err := os.WriteFile(path, body, 0644); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "write failed"})
		return
	}
	c.JSON(http.StatusOK, gin.H{"status": "ok"})
}

func missingHandler(c *gin.Context) {
	dir := getConfigDir()
	var missing []string
	for name, file := range allowedNames {
		p := filepath.Join(dir, file)
		if _, err := os.Stat(p); err != nil {
			if os.IsNotExist(err) {
				missing = append(missing, name)
			}
		}
	}
	c.JSON(http.StatusOK, gin.H{"missing": missing})
}

// ===== History Support =====
type HistoryEntry struct {
	Timestamp string `json:"timestamp"`
	Size      int64  `json:"size"`
}

func historyDirFor(name string) (string, string, error) {
	file, ok := allowedNames[name]
	if !ok {
		return "", "", errors.New("unsupported config name")
	}
	base := getConfigDir()
	dir := filepath.Join(base, ".history", file)
	path := filepath.Join(base, file)
	return dir, path, nil
}

func writeHistory(name string, content []byte, _ string) error {
	dir, _, err := historyDirFor(name)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(dir, 0755); err != nil {
		return err
	}
	ts := time.Now().Format("20060102-150405")
	fname := filepath.Join(dir, ts+".json")
	if err := os.WriteFile(fname, content, 0644); err != nil {
		return err
	}
	// prune old history files, keep latest 6
	const keep = 5
	entries, _ := os.ReadDir(dir)
	var names []string
	for _, de := range entries {
		if de.IsDir() {
			continue
		}
		n := de.Name()
		if strings.HasSuffix(n, ".json") {
			names = append(names, n)
		}
	}
	// timestamps are formatted as YYYYMMDD-HHMMSS; lexicographic sort is chronological
	sort.Strings(names) // oldest first
	if len(names) > keep {
		toDelete := names[:len(names)-keep]
		for _, n := range toDelete {
			_ = os.Remove(filepath.Join(dir, n))
		}
	}
	return nil
}

func listHistory(name string) ([]HistoryEntry, error) {
	dir, _, err := historyDirFor(name)
	if err != nil {
		return nil, err
	}
	f, err := os.ReadDir(dir)
	if err != nil {
		if os.IsNotExist(err) {
			return []HistoryEntry{}, nil
		}
		return nil, err
	}
	var out []HistoryEntry
	for _, de := range f {
		if de.IsDir() {
			continue
		}
		if !strings.HasSuffix(de.Name(), ".json") {
			continue
		}
		info, _ := de.Info()
		ts := strings.TrimSuffix(de.Name(), ".json")
		out = append(out, HistoryEntry{Timestamp: ts, Size: info.Size()})
	}
	// latest first
	for i, j := 0, len(out)-1; i < j; i, j = i+1, j-1 {
		out[i], out[j] = out[j], out[i]
	}
	return out, nil
}

func rollbackHistory(name, timestamp string) error {
	dir, path, err := historyDirFor(name)
	if err != nil {
		return err
	}
	src := filepath.Join(dir, timestamp+".json")
	b, err := os.ReadFile(src)
	if err != nil {
		return err
	}
	return os.WriteFile(path, b, 0644)
}

func getActor(c *gin.Context) string {
	if v, ok := c.Get("user"); ok {
		if s, ok2 := v.(string); ok2 {
			return s
		}
	}
	return "unknown"
}

func listHistoryHandler(c *gin.Context) {
	name := c.Param("name")
	entries, err := listHistory(name)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}
	c.JSON(http.StatusOK, gin.H{"entries": entries})
}

type RollbackRequest struct {
	Timestamp string `json:"timestamp"`
}

func rollbackHistoryHandler(c *gin.Context) {
	name := c.Param("name")
	var req RollbackRequest
	if err := c.ShouldBindJSON(&req); err != nil || req.Timestamp == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "invalid request"})
		return
	}
	if err := rollbackHistory(name, req.Timestamp); err != nil {
		if os.IsNotExist(err) {
			c.JSON(http.StatusNotFound, gin.H{"error": "history not found"})
			return
		}
		c.JSON(http.StatusInternalServerError, gin.H{"error": "rollback failed"})
		return
	}
	c.JSON(http.StatusOK, gin.H{"status": "ok"})
}

// Explicit backup: create a history snapshot from current config file
func backupHistoryHandler(c *gin.Context) {
	name := c.Param("name")
	path, err := resolveConfigPath(name)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}
	b, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			c.JSON(http.StatusNotFound, gin.H{"error": "config not found"})
			return
		}
		c.JSON(http.StatusInternalServerError, gin.H{"error": "read failed"})
		return
	}
	// optional: validate JSON
	if !json.Valid(b) {
		c.JSON(http.StatusBadRequest, gin.H{"error": "invalid json in current config"})
		return
	}
	if err := writeHistory(name, b, getActor(c)); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "backup failed"})
		return
	}
	c.JSON(http.StatusOK, gin.H{"status": "ok"})
}

// ===== users.local =====
func readUsersLocal() (string, string, error) {
	dir := getConfigDir()
	p := filepath.Join(dir, ".users.local")
	b, err := os.ReadFile(p)
	if err != nil {
		return "", "", err
	}
	lines := strings.Split(string(b), "\n")
	var vals []string
	for _, l := range lines {
		s := strings.TrimSpace(l)
		if s != "" {
			vals = append(vals, s)
		}
	}
	if len(vals) < 2 {
		return "", "", errors.New("invalid users.local format")
	}
	return vals[0], vals[1], nil
}
