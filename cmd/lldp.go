package main

import (
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	neturl "net/url"
	"os"
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

// 规范化显示端口名：特例将 "eth0" 统一为 "Ethernet0"
func normalizePortName(s string) string {
	if strings.EqualFold(strings.TrimSpace(s), "eth0") {
		return "Ethernet0"
	}
	return s
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

func defaultPromURLs() []string {
	if v := strings.TrimSpace(os.Getenv("PROM_QUERY_URL")); v != "" {
		return []string{v}
	}
	if v := strings.TrimSpace(os.Getenv("PROM_URL")); v != "" {
		return []string{v}
	}
	return []string{
		"http://10.27.3.68:8481/select/0/prometheus",
		"http://10.102.10.6:9090",
	}
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
	client := http.Client{Timeout: 15 * time.Second}
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
		if upData, err := queryPrometheus(candidate, "up"); err == nil && len(upData.Data.Result) > 0 {
			fmt.Printf("使用监控查询地址: %s\n", strings.TrimRight(candidate, "/"))
			return strings.TrimRight(candidate, "/")
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
	// 查询接口名：用于当本地端口 ID 不可读时回退为 ifName/ifDescr
	// 查询 ifIndex 指标（包含 ifDescr 和 ifName 信息）
	ifIndexQuery := "ifIndex"
	ifIndexData, _ := queryPrometheus(promURL, ifIndexQuery)

	debugPrintf("Result counts -> loc: %d, rem: %d, sys: %d, remDesc: %d, hostSys: %d, ifIndex: %d\n",
		len(locData.Data.Result), len(remData.Data.Result), len(sysData.Data.Result), len(remPortDescData.Data.Result), len(hostSysData.Data.Result), len(ifIndexData.Data.Result))

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
	// 对端端口描述映射（当 lldpRemPortId 为简写时使用）
	remPortDescMap := make(map[string]map[string]string) // host -> localPortNum -> lldpRemPortDesc

	for _, r := range locData.Data.Result {
		host := getHost(r.Metric)
		portNum := r.Metric["lldpLocPortNum"]
		portHex := r.Metric["lldpLocPortId"]
		port := decodeHex(portHex)
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

	// 接口名与描述映射构建（用于来源端口回退）
	for _, r := range ifIndexData.Data.Result {
		inst := strings.TrimSpace(r.Metric["instance"])
		idx := strings.TrimSpace(r.Metric["ifIndex"])
		// 优先使用 ifDescr，如果没有则使用 ifName
		portName := strings.TrimSpace(r.Metric["ifDescr"])
		if portName == "" {
			portName = strings.TrimSpace(r.Metric["ifName"])
		}
		if portName == "" {
			continue
		}

		// 填充 ifDescrMap（保持向后兼容）
		if ifDescrMap[inst] == nil {
			ifDescrMap[inst] = make(map[string]string)
		}
		ifDescrMap[inst][idx] = portName

		// 基于 IP 的聚合，确保以 baseIP+ifIndex 命中端口名
		base := baseInstance(inst)
		if base != "" {
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
			if instancesByBase[base] == nil {
				instancesByBase[base] = make(map[string]bool)
			}
			instancesByBase[base][inst] = true
		}
	}

	// 构建 link
	var links []Link
	seen := make(map[string]bool) // 去重
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
		if remDesc != "" {
			if targetPort == "" || !isPrintableASCII(targetPort) || isAbbreviatedPortName(targetPort) {
				debugPrintf("TargetPort override using lldpRemPortDesc: host=%s localPortNum=%s old=%s new=%s\n", host, localPortNum, targetPort, remDesc)
				targetPort = normalizePortName(remDesc)
			}
		}
		if targetPort == "" || !isPrintableASCII(targetPort) {
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

		// 去重处理，保证 {"A","p1","B","p2"} 与 {"B","p2","A","p1"} 不重复
		key1 := fmt.Sprintf("%s-%s-%s-%s", sourceHost, sourcePort, targetHost, targetPort)
		key2 := fmt.Sprintf("%s-%s-%s-%s", targetHost, targetPort, sourceHost, sourcePort)
		if seen[key1] || seen[key2] {
			debugPrintf("Duplicate link skipped: %s or %s\n", key1, key2)
			continue
		}
		seen[key1] = true

		links = append(links, Link{
			Source:     sourceHost,
			SourcePort: sourcePort,
			Target:     targetHost,
			TargetPort: targetPort,
		})
	}

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
