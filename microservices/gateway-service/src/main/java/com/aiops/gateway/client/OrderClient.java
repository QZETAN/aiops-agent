package com.aiops.gateway.client;

import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;

import java.util.Map;

@FeignClient(name = "order-service", url = "http://localhost:8081")
public interface OrderClient {

    @GetMapping("/order/{userId}")
    Map<String, Object> getOrder(@PathVariable("userId") Long userId);
}
