package com.aiops.gateway.controller;

import com.aiops.gateway.client.OrderClient;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

@RestController
@RequestMapping("/api")
public class ApiController {

    private final OrderClient orderClient;

    public ApiController(OrderClient orderClient) {
        this.orderClient = orderClient;
    }

    @GetMapping("/order/{userId}")
    public Map<String, Object> getOrder(@PathVariable Long userId) {
        return orderClient.getOrder(userId);
    }
}
