package com.example.finance_helper.controller;

import com.example.finance_helper.dto.NewsDTO;
import com.example.finance_helper.service.NewsCrawlerService;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Controller;
import org.springframework.ui.Model;
import org.springframework.web.bind.annotation.GetMapping;

import java.util.List;

@Controller
@RequiredArgsConstructor
public class NewsController {

    private final NewsCrawlerService newsCrawlerService;

    @GetMapping("/news")
    public String getNews(Model model) {
        List<NewsDTO> newsList = newsCrawlerService.getMajorNews();
        model.addAttribute("newsList", newsList);
        return "news";
    }
}
