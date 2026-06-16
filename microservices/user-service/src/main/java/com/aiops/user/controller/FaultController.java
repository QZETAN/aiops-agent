package com.aiops.user.controller;

import org.springframework.web.bind.annotation.*;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * 故障注入控制器 —— 用于模拟典型生产故障，验证 Agent 的诊断能力。
 */
@RestController
@RequestMapping("/fault")
public class FaultController {

    // ==================== 1. CPU 飙升 ====================

    @PostMapping("/cpu")
    public Map<String, String> cpuSpike(
            @RequestParam(defaultValue = "30") int seconds) {

        new Thread(() -> {
            long end = System.currentTimeMillis() + seconds * 1000L;
            while (System.currentTimeMillis() < end) {
                // 空转，吃满一个 CPU 核心
                Math.sqrt(Math.random());
            }
        }).start();

        return Map.of(
                "status", "injected",
                "fault", "cpu_spike",
                "duration", seconds + "s",
                "hint", "一个核心正在满负荷运转"
        );
    }

    // ==================== 2. 内存泄漏 ====================

    private static final List<byte[]> LEAK_LIST = new ArrayList<>();

    @PostMapping("/memory")
    public Map<String, String> memoryLeak(
            @RequestParam(defaultValue = "10") int mbPerCall,
            @RequestParam(defaultValue = "5") int calls) {

        for (int i = 0; i < calls; i++) {
            LEAK_LIST.add(new byte[mbPerCall * 1024 * 1024]);
        }

        return Map.of(
                "status", "injected",
                "fault", "memory_leak",
                "allocated", (mbPerCall * calls) + "MB",
                "total_held", (LEAK_LIST.size() * mbPerCall) + "MB (累计)",
                "hint", "JVM 堆内存将持续增长，直到 OOM"
        );
    }

    // ==================== 3. NullPointerException ====================

    @PostMapping("/npe")
    public Map<String, String> nullPointer() {
        // 故意触发 NPE
        String str = null;
        str.length(); // 💥

        return Map.of("status", "unreachable");
    }

    // ==================== 4. 慢接口 ====================

    @GetMapping("/slow")
    public Map<String, String> slowEndpoint(
            @RequestParam(defaultValue = "5") int seconds) {

        try {
            Thread.sleep(seconds * 1000L);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }

        return Map.of(
                "status", "ok",
                "fault", "slow_response",
                "elapsed", seconds + "s",
                "hint", "用户请求延迟远超正常值"
        );
    }
}
