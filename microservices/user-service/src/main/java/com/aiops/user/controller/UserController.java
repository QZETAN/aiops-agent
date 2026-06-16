package com.aiops.user.controller;

import org.springframework.web.bind.annotation.*;

import java.util.Map;

@RestController
@RequestMapping("/user")
public class UserController {

    @GetMapping("/{id}")
    public Map<String, Object> getUser(@PathVariable Long id) {
        // 模拟随机耗时（20ms ~ 120ms），让 Trace 有可见的 span 耗时
        try {
            Thread.sleep((long) (Math.random() * 100 + 20));
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }

        return Map.of(
                "id", id,
                "name", "用户" + id,
                "email", "user" + id + "@example.com"
        );
    }
}
