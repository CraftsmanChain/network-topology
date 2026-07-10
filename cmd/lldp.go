package main

import (
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	neturl "net/url"
	"os"
	"path/filepath"
	"strings"
	"time"
)

type PromResult struct {
	Status string `json:"status"`
	Data   struct {
		Result []struct {
			Metric map[string]string `json:"metric"`
			Value  []interface{}     `json:"value"`
		} `json:"result"`
	} `json:"data"`
}

type Link struct {
	Source     string `json:"source"`
	SourcePort string `json:"sourcePort"`
	Target     string `json:"target"`
	TargetPort string `json:"targetPort"`
}

func endpointKey(host, port string) string {
	return host + "\x00" + port
}

func undirectedLinkKey(link Link) string {
	left := endpointKey(link.Source, link.SourcePort)
	right := endpointKey(link.Target, link.TargetPort)
	if left <= right {
		return left + "\x01" + right
	}
	return right + "\x01" + left
}

func otherEndpointKey(link Link, current string) string {
	if endpointKey(link.Source, link.SourcePort) == current {
		return endpointKey(link.Target, link.TargetPort)
	}
	return endpointKey(link.Source, link.SourcePort)
}

func preferConflictLink(a, b Link, conflictEndpoint string, pairSupport map[string]int, endpointToLinks map[string][]int) bool {
	supportA := pairSupport[undirectedLinkKey(a)]
	supportB := pairSupport[undirectedLinkKey(b)]
	if supportA != supportB {
		return supportA > supportB
	}

	otherA := otherEndpointKey(a, conflictEndpoint)
	otherB := otherEndpointKey(b, conflictEndpoint)
	conflictsA := len(endpointToLinks[otherA])
	conflictsB := len(endpointToLinks[otherB])
	if conflictsA != conflictsB {
		return conflictsA < conflictsB
	}

	keyA := undirectedLinkKey(a)
	keyB := undirectedLinkKey(b)
	if keyA != keyB {
		return keyA < keyB
	}

	fullA := fmt.Sprintf("%s-%s-%s-%s", a.Source, a.SourcePort, a.Target, a.TargetPort)
	fullB := fmt.Sprintf("%s-%s-%s-%s", b.Source, b.SourcePort, b.Target, b.TargetPort)
	return fullA < fullB
}

func resolvePortConflicts(links []Link, pairSupport map[string]int) []Link {
	if len(links) <= 1 {
		return links
	}

	active := make([]bool, len(links))
	for i := range active {
		active[i] = true
	}

	for {
		endpointToLinks := make(map[string][]int)
		for i, link := range links {
			if !active[i] {
				continue
			}
			srcKey := endpointKey(link.Source, link.SourcePort)
			tgtKey := endpointKey(link.Target, link.TargetPort)
			endpointToLinks[srcKey] = append(endpointToLinks[srcKey], i)
			endpointToLinks[tgtKey] = append(endpointToLinks[tgtKey], i)
		}

		changed := false
		for ep, idxs := range endpointToLinks {
			if len(idxs) <= 1 {
				continue
			}
			best := idxs[0]
			for _, idx := range idxs[1:] {
				if preferConflictLink(links[idx], links[best], ep, pairSupport, endpointToLinks) {
					best = idx
				}
			}
			for _, idx := range idxs {
				if idx == best || !active[idx] {
					continue
				}
				active[idx] = false
				changed = true
				debugPrintf("Conflict link dropped at endpoint=%s keep=%+v drop=%+v support_keep=%d support_drop=%d\n",
					ep, links[best], links[idx], pairSupport[undirectedLinkKey(links[best])], pairSupport[undirectedLinkKey(links[idx])])
			}
		}

		if !changed {
			break
		}
	}

	resolved := make([]Link, 0, len(links))
	for i, link := range links {
		if active[i] {
			resolved = append(resolved, link)
		}
	}
	if len(resolved) != len(links) {
		fmt.Printf("已消解端口冲突链路: %d -> %d\n", len(links), len(resolved))
	}
	return resolved
}

type CollectorConfig struct {
	Version    int              `json:"version"`
	Monitoring MonitoringConfig `json:"monitoring"`
}

type MonitoringConfig struct {
	QueryURLs         []string `json:"query_urls"`
	ProbeQueries      []string `json:"probe_queries"`
	RequestTimeoutSec int      `json:"request_timeout_sec"`
}

func defaultCollectorConfig() CollectorConfig {
	return CollectorConfig{
		Version: 1,
		Monitoring: MonitoringConfig{
			QueryURLs:         []string{},
			ProbeQueries:      []string{"up"},
			RequestTimeoutSec: 15,
		},
	}
}

// 调试辅助：通过环境变量开启调试日志
func debugEnabled() bool {
	v := strings.ToLower(strings.TrimSpace(os.Getenv("DEBUG_LLDP")))
	return v == "1" || v == "true" || v == "yes"
}

func debugPrintf(format string, a ...interface{}) {
	if debugEnabled() {
		fmt.Printf("[DEBUG] "+format, a...)
	}
}

func debugPrintln(a ...interface{}) {
	if debugEnabled() {
		fmt.Println(append([]interface{}{"[DEBUG]"}, a...)...)
	}
}

// 提取 instance 的基础 IP（去掉 :port）
func baseInstance(inst string) string {
	inst = strings.TrimSpace(inst)
	if inst == "" {
		return ""
	}
	if i := strings.Index(inst, ":"); i >= 0 {
		return inst[:i]
	}
	return inst
}

// decodeHex 把十六进制字符串转成普通字符串
func decodeHex(s string) string {
	s = strings.TrimPrefix(s, "0x")
	data, err := hex.DecodeString(s)
	if err != nil {
		return s
	}
	return string(data)
}

// 判断字符串是否为可读的 ASCII（避免出现不可打印或替换符）
func isPrintableASCII(s string) bool {
	if s == "" {
		return false
	}
	for _, r := range s {
		if r == '\uFFFD' { // 替换符
			return false
		}
		if r < 32 || r > 126 { // 非可打印 ASCII
			return false
		}
	}
	return true
}

// 判断是否为厂商常见的简写端口名（例如 "Eth4(Port4)"）
func isAbbreviatedPortName(s string) bool {
	s = strings.TrimSpace(s)
	if s == "" {
		return false
	}
	// 目前按需求识别 "Eth" 前缀且包含 "(Port" 的模式
	if strings.HasPrefix(s, "Eth") && strings.Contains(s, "(Port") {
		return true
	}
	return false
}

func isHexBytePair(s string) bool {
	if len(s) != 2 {
		return false
	}
	for _, r := range s {
		if !((r >= '0' && r <= '9') || (r >= 'a' && r <= 'f') || (r >= 'A' && r <= 'F')) {
			return false
		}
	}
	return true
}

func isMACAddress(s string) bool {
	parts := strings.Split(strings.TrimSpace(s), ":")
	if len(parts) != 6 {
		return false
	}
	for _, part := range parts {
		if !isHexBytePair(part) {
			return false
		}
	}
	return true
}

func insertSpaceAfterPrefix(s string, prefixes []string) string {
	for _, prefix := range prefixes {
		if strings.HasPrefix(s, prefix) {
			rest := strings.TrimSpace(strings.TrimPrefix(s, prefix))
			if rest == "" {
				return prefix
			}
			return prefix + " " + rest
		}
	}
	return s
}

// 规范化显示端口名，统一不同厂商接口名的展示格式。
func normalizePortName(s string) string {
	s = strings.TrimSpace(s)
	if strings.EqualFold(s, "eth0") {
		return "Ethernet0"
	}
	s = insertSpaceAfterPrefix(s, []string{
		"M-GigabitEthernet",
		"FourHundredGigabitEthernet",
		"FourHundredGigE",
		"HundredGigabitEthernet",
		"HundredGigE",
		"FHGigabitEthernet",
		"Ten-GigabitEthernet",
		"TenGigabitEthernet",
		"FortyGigabitEthernet",
		"FortyGigE",
		"TwentyFiveGigE",
		"TwentyFiveGigabitEthernet",
		"25GE",
		"100GE",
		"40GE",
		"10GE",
		"GigabitEthernet",
	})
	return s
}

func shouldResolvePortName(s string) bool {
	s = strings.TrimSpace(s)
	return s == "" || !isPrintableASCII(s) || isAbbreviatedPortName(s) || isMACAddress(s)
}

func isLikelyInterfaceName(s string) bool {
	s = strings.TrimSpace(s)
	if s == "" {
		return false
	}
	if strings.HasPrefix(s, "Link-to-") || strings.Contains(s, "(Port") {
		return false
	}
	lower := strings.ToLower(s)
	if lower == "eth7x" {
		return false
	}
	if lower == "eth0" {
		return true
	}
	prefixes := []string{
		"ethernet",
		"fourhundredgige",
		"hundredgige",
		"fhgigabitethernet",
		"gigabitethernet",
		"m-gigabitethernet",
		"port-channel",
		"vlan",
		"loopback",
		"inloopback",
	}
	for _, prefix := range prefixes {
		if strings.HasPrefix(lower, prefix) {
			return true
		}
	}
	return false
}

// 根据指标标签提取主机标识，优先使用 hostname，缺失则回退到 instance
func getHost(m map[string]string) string {
	if h := strings.TrimSpace(m["hostname"]); h != "" {
		return h
	}
	// 可扩展其它可能的设备名标签（如 device/agent_host），当前回退到 instance
	if inst := strings.TrimSpace(m["instance"]); inst != "" {
		return inst
	}
	return ""
}

func normalizeQueryURL(url string) string {
	url = strings.TrimSpace(url)
	if url == "" {
		return ""
	}
	if !strings.HasSuffix(strings.TrimRight(url, "/"), "/api/v1/query") {
		url = strings.TrimRight(url, "/") + "/api/v1/query"
	}
	return url
}

func collectorConfigCandidates() []string {
	candidates := []string{}
	if v := strings.TrimSpace(os.Getenv("COLLECTOR_CONFIG")); v != "" {
		candidates = append(candidates, v)
	}
	if v := strings.TrimSpace(os.Getenv("CONFIG_DIR")); v != "" {
		candidates = append(candidates, filepath.Join(v, "collector_config.json"))
	}
	if wd, err := os.Getwd(); err == nil {
		candidates = append(candidates, filepath.Join(wd, "config", "collector_config.json"))
	}
	return candidates
}

func loadCollectorConfig() CollectorConfig {
	cfg := defaultCollectorConfig()
	for _, path := range collectorConfigCandidates() {
		body, err := os.ReadFile(path)
		if err != nil {
			continue
		}
		var loaded CollectorConfig
		if err := json.Unmarshal(body, &loaded); err != nil {
			fmt.Printf("[WARNING] 读取采集配置失败 %s: %v\n", path, err)
			continue
		}
		if loaded.Version != 0 {
			cfg.Version = loaded.Version
		}
		if len(loaded.Monitoring.QueryURLs) > 0 {
			cfg.Monitoring.QueryURLs = loaded.Monitoring.QueryURLs
		}
		if len(loaded.Monitoring.ProbeQueries) > 0 {
			cfg.Monitoring.ProbeQueries = loaded.Monitoring.ProbeQueries
		}
		if loaded.Monitoring.RequestTimeoutSec > 0 {
			cfg.Monitoring.RequestTimeoutSec = loaded.Monitoring.RequestTimeoutSec
		}
		return cfg
	}
	return cfg
}

func requestTimeout() time.Duration {
	sec := loadCollectorConfig().Monitoring.RequestTimeoutSec
	if sec <= 0 {
		sec = 15
	}
	return time.Duration(sec) * time.Second
}

func probeQueries() []string {
	values := loadCollectorConfig().Monitoring.ProbeQueries
	result := make([]string, 0, len(values))
	for _, value := range values {
		if trimmed := strings.TrimSpace(value); trimmed != "" {
			result = append(result, trimmed)
		}
	}
	if len(result) == 0 {
		return []string{"up"}
	}
	return result
}

func defaultPromURLs() []string {
	if v := strings.TrimSpace(os.Getenv("PROM_QUERY_URL")); v != "" {
		return []string{normalizeQueryURL(v)}
	}
	if v := strings.TrimSpace(os.Getenv("PROM_URL")); v != "" {
		return []string{normalizeQueryURL(v)}
	}
	result := []string{}
	for _, value := range loadCollectorConfig().Monitoring.QueryURLs {
		if normalized := normalizeQueryURL(value); normalized != "" {
			result = append(result, normalized)
		}
	}
	return result
}

func buildQueryURL(promURL, metric string) string {
	promURL = strings.TrimRight(strings.TrimSpace(promURL), "/")
	if !strings.HasSuffix(promURL, "/api/v1/query") {
		promURL += "/api/v1/query"
	}
	values := neturl.Values{}
	values.Set("query", metric)
	return promURL + "?" + values.Encode()
}

// queryPrometheus 查询 Prometheus/VictoriaMetrics 指标
func queryPrometheus(promURL, metric string) (PromResult, error) {
	queryURL := buildQueryURL(promURL, metric)
	debugPrintf("Querying: %s\n", queryURL)
	client := http.Client{Timeout: requestTimeout()}
	resp, err := client.Get(queryURL)
	if err != nil {
		debugPrintf("HTTP error: %v\n", err)
		return PromResult{}, err
	}
	defer resp.Body.Close()
	debugPrintf("HTTP status: %s (%d)\n", resp.Status, resp.StatusCode)
	body, _ := io.ReadAll(resp.Body)
	var result PromResult
	err = json.Unmarshal(body, &result)
	if err != nil {
		debugPrintf("JSON unmarshal error: %v\n", err)
	}
	if result.Status != "success" {
		debugPrintf("Prometheus response status: %s\n", result.Status)
	}
	return result, err
}

func choosePromURL() string {
	for _, candidate := range defaultPromURLs() {
		for _, probeQuery := range probeQueries() {
			if upData, err := queryPrometheus(candidate, probeQuery); err == nil && len(upData.Data.Result) > 0 {
				fmt.Printf("使用监控查询地址: %s\n", strings.TrimRight(candidate, "/"))
				return strings.TrimRight(candidate, "/")
			}
		}
	}
	return ""
}

func main() {
	promURL := choosePromURL()
	if promURL == "" {
		fmt.Println("Prometheus/VictoriaMetrics 当前不可达或无 up 指标，未更新 links-raw.json 与 links.json")
		return
	}

	locData, _ := queryPrometheus(promURL, "lldpLocPortId")
	remData, _ := queryPrometheus(promURL, "lldpRemPortId")
	sysData, _ := queryPrometheus(promURL, "lldpRemSysName")
	// 查询对端端口描述，用于在遇到简写端口名时覆盖为完整名称
	remPortDescData, _ := queryPrometheus(promURL, "lldpRemPortDesc")
	// 额外查询：本机 sysName 指标（标签 sysName）用于 Source 名称统一
	hostSysData, _ := queryPrometheus(promURL, "sysName")
	// 查询接口元数据：ifHighSpeed 标签上通常携带 ifDescr/ifName/ifAlias，
	// 比 ifIndex 在该集群更稳定，可同时用于源端口和对端别名反查。
	ifMetaQuery := "ifHighSpeed"
	ifMetaData, _ := queryPrometheus(promURL, ifMetaQuery)

	debugPrintf("Result counts -> loc: %d, rem: %d, sys: %d, remDesc: %d, hostSys: %d, ifMeta: %d\n",
		len(locData.Data.Result), len(remData.Data.Result), len(sysData.Data.Result), len(remPortDescData.Data.Result), len(hostSysData.Data.Result), len(ifMetaData.Data.Result))

	// 构建映射
	locMap := make(map[string]map[string]string)        // host -> portNum -> sourcePort
	locByInstance := make(map[string]map[string]string) // instance -> portNum -> decoded lldpLocPortId
	sysNameMap := make(map[string]map[string]string)    // host -> portNum -> targetHost
	localSysNameByHost := make(map[string]string)       // host/instance -> local sysName
	instanceByHost := make(map[string]string)           // hostname/instance -> instance IP:port
	sysNameToInstance := make(map[string]string)        // sysName -> instance IP:port
	ifDescrMap := make(map[string]map[string]string)    // instance -> ifIndex -> ifDescr
	// 基于 IP 的聚合映射，便于以 baseIP+ifIndex 命中 ifDescr/ifName
	instancesByBase := make(map[string]map[string]bool)        // baseIP -> set(instance)
	ifNameByBase := make(map[string]map[string]string)         // baseIP -> ifIndex -> ifName
	ifDescrByBase := make(map[string]map[string]string)        // baseIP -> ifIndex -> ifDescr
	instForIfDescrByBase := make(map[string]map[string]string) // baseIP -> ifIndex -> instance
	portByAliasByBase := make(map[string]map[string]string)    // baseIP -> ifAlias -> portName
	// 对端端口描述映射（当 lldpRemPortId 为简写时使用）
	remPortDescMap := make(map[string]map[string]string) // host -> localPortNum -> lldpRemPortDesc

	for _, r := range locData.Data.Result {
		host := getHost(r.Metric)
		portNum := r.Metric["lldpLocPortNum"]
		portHex := r.Metric["lldpLocPortId"]
		port := normalizePortName(decodeHex(portHex))
		if locMap[host] == nil {
			locMap[host] = make(map[string]string)
		}
		locMap[host][portNum] = port
		// 记录 hostname -> instance 映射（同时为 instance 自身建立映射）
		if inst := strings.TrimSpace(r.Metric["instance"]); inst != "" {
			if host != "" {
				instanceByHost[host] = inst
			}
			instanceByHost[inst] = inst
			// 维护 baseIP -> instances 集合
			base := baseInstance(inst)
			if base != "" {
				if instancesByBase[base] == nil {
					instancesByBase[base] = make(map[string]bool)
				}
				instancesByBase[base][inst] = true
			}
			// 以 instance 为键，记录本端端口ID的十六进制解码，供对端端口名使用
			if locByInstance[inst] == nil {
				locByInstance[inst] = make(map[string]string)
			}
			locByInstance[inst][portNum] = port
		}
	}

	for _, r := range sysData.Data.Result {
		host := getHost(r.Metric)
		portNum := r.Metric["lldpRemLocalPortNum"]
		sys := r.Metric["lldpRemSysName"]
		if sysNameMap[host] == nil {
			sysNameMap[host] = make(map[string]string)
		}
		sysNameMap[host][portNum] = sys
		// 补充 hostname -> instance 映射
		if inst := strings.TrimSpace(r.Metric["instance"]); inst != "" {
			if host != "" {
				instanceByHost[host] = inst
			}
			instanceByHost[inst] = inst
			base := baseInstance(inst)
			if base != "" {
				if instancesByBase[base] == nil {
					instancesByBase[base] = make(map[string]bool)
				}
				instancesByBase[base][inst] = true
			}
		}
	}

	// 记录对端端口描述映射（用于覆盖简写的对端端口名称）
	for _, r := range remPortDescData.Data.Result {
		host := getHost(r.Metric)
		portNum := strings.TrimSpace(r.Metric["lldpRemLocalPortNum"])
		desc := strings.TrimSpace(r.Metric["lldpRemPortDesc"])
		if desc == "" {
			continue
		}
		if remPortDescMap[host] == nil {
			remPortDescMap[host] = make(map[string]string)
		}
		remPortDescMap[host][portNum] = desc
	}

	// 本机 sysName 映射（用于 Source 名称统一）
	for _, r := range hostSysData.Data.Result {
		host := getHost(r.Metric)
		sys := strings.TrimSpace(r.Metric["sysName"])
		inst := strings.TrimSpace(r.Metric["instance"])
		if sys != "" {
			localSysNameByHost[host] = sys
			if inst != "" {
				sysNameToInstance[sys] = inst
				// 维护 baseIP -> instances 集合
				base := baseInstance(inst)
				if base != "" {
					if instancesByBase[base] == nil {
						instancesByBase[base] = make(map[string]bool)
					}
					instancesByBase[base][inst] = true
				}
			}
		}
	}

	// 接口名与别名映射构建（用于来源端口回退与对端端口反查）
	for _, r := range ifMetaData.Data.Result {
		inst := strings.TrimSpace(r.Metric["instance"])
		idx := strings.TrimSpace(r.Metric["ifIndex"])
		if inst == "" || idx == "" {
			continue
		}
		ifName := normalizePortName(strings.TrimSpace(r.Metric["ifName"]))
		portName := normalizePortName(strings.TrimSpace(r.Metric["ifDescr"]))
		if portName == "" {
			portName = ifName
		}
		if portName == "" {
			continue
		}
		alias := strings.TrimSpace(r.Metric["ifAlias"])

		// 填充 ifDescrMap（保持向后兼容）
		if ifDescrMap[inst] == nil {
			ifDescrMap[inst] = make(map[string]string)
		}
		ifDescrMap[inst][idx] = portName

		// 基于 IP 的聚合，确保以 baseIP+ifIndex 命中端口名
		base := baseInstance(inst)
		if base != "" {
			if ifNameByBase[base] == nil {
				ifNameByBase[base] = make(map[string]string)
			}
			if ifDescrByBase[base] == nil {
				ifDescrByBase[base] = make(map[string]string)
			}
			if instForIfDescrByBase[base] == nil {
				instForIfDescrByBase[base] = make(map[string]string)
			}
			if _, ok := ifDescrByBase[base][idx]; !ok {
				ifDescrByBase[base][idx] = portName
				instForIfDescrByBase[base][idx] = inst
			}
			if ifName != "" {
				ifNameByBase[base][idx] = ifName
			}
			if alias != "" {
				if portByAliasByBase[base] == nil {
					portByAliasByBase[base] = make(map[string]string)
				}
				portByAliasByBase[base][alias] = portName
			}
			if instancesByBase[base] == nil {
				instancesByBase[base] = make(map[string]bool)
			}
			instancesByBase[base][inst] = true
		}
	}

	// 构建 link
	var links []Link
	seenPairs := make(map[string]bool)        // 无向 pair 去重
	seenObservations := make(map[string]bool) // 同方向重复观测去重
	pairSupport := make(map[string]int)       // 记录双向互认强度
	for _, r := range remData.Data.Result {
		host := getHost(r.Metric)
		localPortNum := strings.TrimSpace(r.Metric["lldpRemLocalPortNum"])
		// Source 优先使用本机 sysName，否则回退到 host
		sourceHost := localSysNameByHost[host]
		if sourceHost == "" {
			sourceHost = host
		}
		// 通过 sysName/host 推导 baseIP 候选，严格以 baseIP+ifIndex 命中 ifDescr
		baseCandidates := []string{
			baseInstance(sysNameToInstance[sourceHost]),
			baseInstance(instanceByHost[sourceHost]),
			baseInstance(instanceByHost[host]),
			baseInstance(strings.TrimSpace(r.Metric["instance"])),
			baseInstance(host),
		}
		chosenBase := ""
		reason := ""
		// 1) 优先选择能命中 ifDescr 的 baseIP
		for _, b := range baseCandidates {
			if b == "" {
				continue
			}
			if ifDescrByBase[b] != nil && ifDescrByBase[b][localPortNum] != "" {
				chosenBase = b
				reason = "ifDescr(base)"
				break
			}
		}
		// 2) 其次选择能命中 ifName 的 baseIP
		if chosenBase == "" {
			for _, b := range baseCandidates {
				if b == "" {
					continue
				}
				if ifNameByBase[b] != nil && ifNameByBase[b][localPortNum] != "" {
					chosenBase = b
					reason = "ifName(base)"
					break
				}
			}
		}
		// 3) 最后回退到第一个非空 baseIP
		if chosenBase == "" {
			for _, b := range baseCandidates {
				if b != "" {
					chosenBase = b
					reason = "fallback(base)"
					break
				}
			}
		}
		// 选定用于对端端口名的具体 instance（若以 ifDescr 命中，则取对应实例）
		inst := instForIfDescrByBase[chosenBase][localPortNum]
		if inst == "" {
			// 若未通过 ifDescr 命中，取该 base 下任一已知实例
			if set, ok := instancesByBase[chosenBase]; ok {
				for k := range set {
					inst = k
					break
				}
			}
		}
		debugPrintf("Chosen base: %s, instance=%s (reason=%s) sourceHost=%s localPortNum=%s\n", chosenBase, inst, reason, sourceHost, localPortNum)
		// 本端端口名：严格优先 ifDescr(baseIP+ifIndex)
		sourcePort := ifDescrByBase[chosenBase][localPortNum]
		if sourcePort == "" {
			// 若 ifDescr 缺失，优先回退到 ifName(baseIP+ifIndex)
			ifName := ifNameByBase[chosenBase][localPortNum]
			if ifName != "" {
				debugPrintf("SourcePort fallback to ifName(base): host=%s base=%s portNum=%s name=%s\n", host, chosenBase, localPortNum, ifName)
				sourcePort = ifName
			} else {
				// 其次回退到本端 lldpLocPortId 解码（同一 base 的实例集合中查找）
				sp := ""
				if inst != "" {
					sp = locByInstance[inst][localPortNum]
				}
				if sp == "" {
					if set, ok := instancesByBase[chosenBase]; ok {
						for k := range set {
							if v := locByInstance[k][localPortNum]; v != "" {
								sp = v
								break
							}
						}
					}
				}
				if sp != "" && isPrintableASCII(sp) {
					debugPrintf("SourcePort fallback to lldpLocPortId(base): host=%s base=%s portNum=%s name=%s\n", host, chosenBase, localPortNum, sp)
					sourcePort = sp
				} else {
					// 最后退到 Port<Num>
					fallback := fmt.Sprintf("Port%s", localPortNum)
					debugPrintf("SourcePort fallback to Port#: host=%s base=%s portNum=%s value=%s\n", host, chosenBase, localPortNum, fallback)
					sourcePort = fallback
				}
			}
		}
		targetHost := sysNameMap[host][localPortNum]
		// 对端端口名必须来自当前 LLDP 邻居记录。不能遍历对端设备所有端口取第一个，
		// Go map 遍历顺序不稳定，会把链路随机绑定到错误的对端端口。
		targetPort := normalizePortName(strings.TrimSpace(decodeHex(r.Metric["lldpRemPortId"])))

		// 覆写简写的对端端口名：若 lldpRemPortId 为类似 "Eth4(Port4)" 的简写或不可读，
		// 且存在同一条 LLDP 记录的 lldpRemPortDesc，则使用 lldpRemPortDesc。
		remDesc := ""
		if m := remPortDescMap[host]; m != nil {
			remDesc = strings.TrimSpace(m[localPortNum])
		}
		if shouldResolvePortName(targetPort) && remDesc != "" {
			if isLikelyInterfaceName(remDesc) {
				debugPrintf("TargetPort resolved via lldpRemPortDesc: host=%s target=%s localPortNum=%s desc=%s\n", host, targetHost, localPortNum, remDesc)
				targetPort = normalizePortName(remDesc)
			} else {
				targetBaseCandidates := []string{
					baseInstance(sysNameToInstance[targetHost]),
					baseInstance(instanceByHost[targetHost]),
					baseInstance(targetHost),
				}
				for _, targetBase := range targetBaseCandidates {
					if targetBase == "" {
						continue
					}
					if resolved := strings.TrimSpace(portByAliasByBase[targetBase][remDesc]); resolved != "" {
						debugPrintf("TargetPort resolved via ifAlias: host=%s target=%s base=%s localPortNum=%s alias=%s port=%s\n", host, targetHost, targetBase, localPortNum, remDesc, resolved)
						targetPort = normalizePortName(resolved)
						break
					}
				}
			}
		}
		if targetPort == "" || !isPrintableASCII(targetPort) || isMACAddress(targetPort) {
			fallback := fmt.Sprintf("Port%s", localPortNum)
			debugPrintf("TargetPort fallback to Port#: source=%s target=%s instance=%s portNum=%s value=%s\n", sourceHost, targetHost, inst, localPortNum, fallback)
			targetPort = fallback
		}

		if sourceHost == "" || sourcePort == "" || targetHost == "" || targetPort == "" {
			if debugEnabled() {
				missing := []string{}
				if sourceHost == "" {
					missing = append(missing, "sourceHost")
				}
				if sourcePort == "" {
					missing = append(missing, "sourcePort")
				}
				if targetHost == "" {
					missing = append(missing, "targetHost")
				}
				if targetPort == "" {
					missing = append(missing, "targetPort")
				}
				debugPrintf("Skip link host=%s localPortNum=%s missing=%v\n", host, localPortNum, missing)
			}
			continue
		}

		link := Link{
			Source:     sourceHost,
			SourcePort: sourcePort,
			Target:     targetHost,
			TargetPort: targetPort,
		}
		directedKey := fmt.Sprintf("%s-%s-%s-%s", sourceHost, sourcePort, targetHost, targetPort)
		if seenObservations[directedKey] {
			debugPrintf("Duplicate observation skipped: %s\n", directedKey)
			continue
		}
		seenObservations[directedKey] = true

		pairKey := undirectedLinkKey(link)
		pairSupport[pairKey]++
		if seenPairs[pairKey] {
			debugPrintf("Reverse observation merged into pair: %s (support=%d)\n", pairKey, pairSupport[pairKey])
			continue
		}
		seenPairs[pairKey] = true
		links = append(links, link)
	}

	links = resolvePortConflicts(links, pairSupport)
	debugPrintf("Built links: %d\n", len(links))

	// 若未获取到任何链路数据，则只打印提醒信息，不更新文件
	if len(links) == 0 {
		fmt.Println("未获取到任何链路数据，未更新 links-raw.json 与 links.json")
		return
	}

	// 写入原始链路到 links-raw.json
	rawBytes, _ := json.MarshalIndent(links, "", "  ")
	_ = os.WriteFile("links-raw.json", rawBytes, 0644)
	debugPrintln("Wrote links-raw.json")

	// devices.json 由独立设备同步脚本维护，这里仅读取其结果用于链路过滤。
	devicesPath := "devices.json"
	var devices []string
	existing := make(map[string]bool)
	if b, err := os.ReadFile(devicesPath); err == nil {
		if len(b) > 0 {
			_ = json.Unmarshal(b, &devices)
			for _, d := range devices {
				existing[d] = true
			}
		}
	}
	debugPrintf("Loaded devices.json: %d items\n", len(devices))

	// 使用 devices.json 中维护的设备作为允许集合。
	// 若文件缺失或为空，则不过滤，避免把 LLDP 原始结果全部丢掉。
	allowed := existing

	// 过滤链路
	var filtered []Link
	for _, l := range links {
		if len(allowed) == 0 || (allowed[l.Source] && allowed[l.Target]) {
			filtered = append(filtered, l)
		}
	}

	debugPrintf("Filtered links: %d\n", len(filtered))

	// 若过滤后无数据，则只打印提醒信息，不更新 links.json
	if len(filtered) == 0 {
		fmt.Println("过滤后无数据，未更新 links.json")
	} else {
		// 写入过滤结果到 links.json
		filteredBytes, _ := json.MarshalIndent(filtered, "", "  ")
		_ = os.WriteFile("links.json", filteredBytes, 0644)
		debugPrintln("Wrote links.json")
	}
}
