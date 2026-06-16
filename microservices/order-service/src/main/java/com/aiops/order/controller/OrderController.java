package com.aiops.order.controller;

import com.aiops.order.client.UserClient;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

@RestController
@RequestMapping("/order")
public class OrderController {

    private final UserClient userClient;

    public OrderController(UserClient userClient) {
        this.userClient = userClient;
    }

    @GetMapping("/{userId}")
    public Map<String, Object> getOrder(@PathVariable Long userId) {
        // 调用 user-service 获取用户信息
        Map<String, Object> user = userClient.getUser(userId);

        // 模拟订单数据
        return Map.of(
                "orderId", System.currentTimeMillis(),
                "userId", userId,
                "userName", user.get("name"),
                "amount", 99.99,
                "status", "PAID"
        );
    }
}
