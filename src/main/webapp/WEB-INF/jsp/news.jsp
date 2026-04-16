<%@ page contentType="text/html;charset=UTF-8" language="java" %>
<%@ taglib prefix="c" uri="jakarta.tags.core" %>
<!DOCTYPE html>
<html>
<head>
    <title>주요 금융 뉴스 분석</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 20px; background-color: #f4f7f6; }
        h1 { color: #333; text-align: center; }
        .news-container { max-width: 1000px; margin: 0 auto; }
        .news-card {
            background: white;
            border-radius: 8px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            margin-bottom: 20px;
            padding: 20px;
            overflow: hidden;
        }
        .news-title { font-size: 1.5em; font-weight: bold; margin-bottom: 10px; }
        .news-title a { color: #2c3e50; text-decoration: none; }
        .news-title a:hover { text-decoration: underline; color: #3498db; }
        .news-meta { color: #7f8c8d; font-size: 0.9em; margin-bottom: 15px; }
        .section { margin-bottom: 15px; padding: 10px; background: #f9f9f9; border-left: 4px solid #ddd; border-radius: 4px; }
        .section-title { font-weight: bold; margin-bottom: 5px; display: block; color: #555; }
        .recommendations { border-left-color: #2ecc71; background: #e8f8f5; }
        .cautions { border-left-color: #e74c3c; background: #fdedec; }
        .outlook { border-left-color: #3498db; background: #ebf5fb; }
        .empty-text { color: #aaa; font-style: italic; }
    </style>
</head>
<body>

<div class="news-container">
    <h1>📉 주요 금융 뉴스 분석 요약 📈</h1>

    <c:forEach var="news" items="${newsList}">
        <div class="news-card">
            <div class="news-title">
                <a href="${news.url}" target="_blank">${news.title}</a>
            </div>
            <div class="news-meta">
                <span>출처: ${news.source}</span> | <span>${news.publishedDate}</span>
            </div>
            
            <div class="section">
                <span class="section-title">📝 요약</span>
                ${news.summary}
            </div>

            <div class="section recommendations">
                <span class="section-title">👍 추천 / 호재</span>
                <c:choose>
                    <c:when test="${not empty news.recommendations}">
                        ${news.recommendations}
                    </c:when>
                    <c:otherwise>
                        <span class="empty-text">특이사항 없음</span>
                    </c:otherwise>
                </c:choose>
            </div>

            <div class="section cautions">
                <span class="section-title">⚠️ 조심해야 할 점 / 리스크</span>
                <c:choose>
                    <c:when test="${not empty news.cautions}">
                        ${news.cautions}
                    </c:when>
                    <c:otherwise>
                        <span class="empty-text">특이사항 없음</span>
                    </c:otherwise>
                </c:choose>
            </div>

            <div class="section outlook">
                <span class="section-title">🔮 향후 전망</span>
                <c:choose>
                    <c:when test="${not empty news.outlook}">
                        ${news.outlook}
                    </c:when>
                    <c:otherwise>
                        <span class="empty-text">특이사항 없음</span>
                    </c:otherwise>
                </c:choose>
            </div>
        </div>
    </c:forEach>

    <c:if test="${empty newsList}">
        <p style="text-align: center;">현재 표시할 뉴스가 없습니다.</p>
    </c:if>
</div>

</body>
</html>
