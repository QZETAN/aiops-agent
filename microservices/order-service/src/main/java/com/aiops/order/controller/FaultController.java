package com.aiops.order.controller;

import org.springframework.web.bind.annotation.*;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * 故障注入控制器 —— order-service 专属故障。
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
                Math.sqrt(Math.random());
            }
        }).start();

        return Map.of(
                "status", "injected",
                "fault", "cpu_spike",
                "duration", seconds + "s"
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
                "allocated", (mbPerCall * calls) + "MB"
        );
    }

    // ==================== 3. NullPointerException ====================

    @PostMapping("/npe")
    public Map<String, String> nullPointer() {
        String str = null;
        str.length();
        return Map.of("status", "unreachable");
    }

    // ==================== 4. 慢接口（模拟慢 SQL / DB 延迟）====================

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
                "fault", "slow_db_query",
                "elapsed", seconds + "s"
        );
    }

    // ==================== 5. 超时（模拟下游不响应）====================

    @GetMapping("/timeout")
    public Map<String, String> timeout() {
        // 模拟一个永远不会返回的调用
        try {
            Thread.sleep(60_000); // 60 秒
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
        return Map.of("status", "never_reached");
    }
}
