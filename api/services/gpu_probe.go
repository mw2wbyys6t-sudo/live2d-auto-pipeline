package services

import (
	"context"
	"fmt"
	"time"
)

// bridgeGPUProbeScript 直接调用 Python 侧唯一真源 core.gpu_probe。
//
// 判定逻辑不在 Go 再写一份：两边各自实现「有没有可用 GPU」迟早会给出
// 不同结论，同一台机器换后端时自检结果就会跳变。
const bridgeGPUProbeScript = `
import json
from core.gpu_probe import probe_gpu
print(json.dumps(probe_gpu()))
`

// gpuProbeCheckTTL 与 Python 后端的探针缓存保持一致（30s）。
const gpuProbeCheckTTL = 30 * time.Second

// gpuProbeTimeout 覆盖解释器启动 + WMI 枚举（枚举侧内部限时 8s）。
const gpuProbeTimeout = 30 * time.Second

// ProbeGPU 返回 GPU 硬件枚举、本进程可用的推理运行时与推荐设备。
//
// 结果按 TTL 缓存：探测要起 Python 子进程并跑一次 WMI 查询，自检页轮询时
// 每次都重跑会把响应拖到秒级。探测失败不缓存，下一次调用立即重探。
func (pb *PythonBridge) ProbeGPU(ctx context.Context) (map[string]interface{}, error) {
	pb.gpuMu.Lock()
	defer pb.gpuMu.Unlock()

	if pb.gpuReport != nil && time.Since(pb.gpuCheckedAt) < gpuProbeCheckTTL {
		return pb.gpuReport, nil
	}

	res, err := pb.runBridgeTask(ctx, bridgeGPUProbeScript, nil, gpuProbeTimeout)
	if err != nil {
		return nil, err
	}
	report, ok := res["result"].(map[string]interface{})
	if !ok {
		return nil, fmt.Errorf("GPU 探测返回了意外的结果结构")
	}

	pb.gpuReport = report
	pb.gpuCheckedAt = time.Now()
	return report, nil
}

// InvalidateGPUProbe 让下一次 ProbeGPU 立即重跑探测。
// 用户在设置页装完 CUDA/DirectML 运行时后需要马上看到新结论，
// 等 TTL 自然过期会让人以为安装没生效。
func (pb *PythonBridge) InvalidateGPUProbe() {
	pb.gpuMu.Lock()
	pb.gpuReport = nil
	pb.gpuCheckedAt = time.Time{}
	pb.gpuMu.Unlock()
}
