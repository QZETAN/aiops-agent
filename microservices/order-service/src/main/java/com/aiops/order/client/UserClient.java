package com.aiops.order.client;

import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;

import java.util.Map;

@FeignClient(name = "user-service", url = "http://localhost:8082")
public interface UserClient {

    @GetMapping("/user/{id}")
    Map<String, Object> getUser(@PathVariable("id") Long id);
}
