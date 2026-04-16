package com.example.finance_helper.dto;

import lombok.Data;
import lombok.Builder;
import lombok.NoArgsConstructor;
import lombok.AllArgsConstructor;

@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class NewsDTO {
    private String title;
    private String url;
    private String summary;
    private String recommendations;
    private String cautions;
    private String outlook;
    private String publishedDate;
    private String source;
}
