package com.example.finance_helper.service;

import com.example.finance_helper.dto.NewsDTO;
import org.jsoup.Jsoup;
import org.jsoup.nodes.Document;
import org.jsoup.nodes.Element;
import org.jsoup.select.Elements;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

@Service
public class NewsCrawlerService {

    private static final String NAVER_FINANCE_NEWS_URL = "https://finance.naver.com/news/mainnews.naver";

    public List<NewsDTO> getMajorNews() {
        List<NewsDTO> newsList = new ArrayList<>();
        try {
            Document doc = Jsoup.connect(NAVER_FINANCE_NEWS_URL).get();
            Elements newsItems = doc.select(".newsList li dl");

            for (Element item : newsItems) {
                Element link = item.select("dt a").last(); // Sometimes there's an image in first dt
                if (link == null)
                    link = item.select("dt a").first(); // Fallback
                if (link == null)
                    continue; // Skip if no link found

                String title = link.text();
                String relativeUrl = link.attr("href");
                String fullUrl = "https://finance.naver.com" + relativeUrl;

                String summaryText = item.select("dd.articleSummary").text();
                // Remove the press name and date often found at the end of summary
                summaryText = summaryText.replaceAll("\\d{4}-\\d{2}-\\d{2}.*", "").trim();

                NewsDTO newsDTO = analyzeNews(fullUrl, title);
                if (newsDTO != null) {
                    newsList.add(newsDTO);
                }

                // Limit to 5 items for performance
                if (newsList.size() >= 5)
                    break;
            }
        } catch (IOException e) {
            e.printStackTrace();
        }
        return newsList;
    }

    private NewsDTO analyzeNews(String url, String title) {
        try {
            Document doc = Jsoup.connect(url).get();
            Element contentElement = doc.selectFirst("#content"); // Naver News content area
            if (contentElement == null)
                return null;

            String content = contentElement.text();

            return NewsDTO.builder()
                    .title(title)
                    .url(url)
                    .summary(extractSummary(content))
                    .recommendations(extractByKeywords(content, "추천", "매수", "유망", "호재"))
                    .cautions(extractByKeywords(content, "주의", "하락", "리스크", "우려", "매도"))
                    .outlook(extractByKeywords(content, "전망", "예상", "기대"))
                    .publishedDate(doc.select(".article_info .article_date").text())
                    .source("Naver Finance")
                    .build();

        } catch (IOException e) {
            e.printStackTrace();
            return null;
        }
    }

    private String extractSummary(String content) {
        // Simple summary: First 200 chars or first sentence
        if (content.length() > 200) {
            return content.substring(0, 200) + "...";
        }
        return content;
    }

    private String extractByKeywords(String content, String... keywords) {
        // Split by sentences (simplified)
        String[] sentences = content.split("(?<=[.?!])\\s+");
        StringBuilder extracted = new StringBuilder();

        for (String sentence : sentences) {
            for (String keyword : keywords) {
                if (sentence.contains(keyword)) {
                    if (extracted.length() > 0)
                        extracted.append(" ");
                    extracted.append(sentence.trim());
                    break; // Move to next sentence to avoid duplicating same sentence
                }
            }
            if (extracted.length() > 300)
                break; // Limit length
        }

        if (extracted.length() == 0)
            return "특이사항 없음";
        return extracted.toString();
    }
}
