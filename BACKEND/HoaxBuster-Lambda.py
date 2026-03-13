import os
import time
import json
import boto3
import logging
import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
from botocore.exceptions import ClientError
from bs4 import BeautifulSoup
from decimal import Decimal
from dotenv import load_dotenv
import hashlib

# ---- Configuration ----
DDB_TABLE_NAME = "NewsArticle"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY").strip()
GEMINI_API_URL = os.getenv("GEMINI_API_URL")

# Initialize AWS clients
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(DDB_TABLE_NAME)

# Setup logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# CORS headers for all responses
CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "POST,OPTIONS"
}

# UPDATED: Helper function to count words
def count_words(text):
    """Count words in text"""
    if not text:
        return 0
    return len(text.split())

# UPDATED: Helper function to create content hash for caching
def create_content_hash(content):
    """Create a hash of the content for caching purposes"""
    if not content:
        return None
    # Normalize content: strip whitespace, convert to lowercase for consistent hashing
    normalized_content = content.strip().lower()
    return hashlib.md5(normalized_content.encode('utf-8')).hexdigest()



# Add this new function after the existing helper functions (around line 60-70)
def generate_intelligent_summary(content, max_length=200):
    """Generate an intelligent summary using Gemini API"""
    if not content or len(content) <= max_length:
        return content
    
    # If Gemini API is not available, fall back to simple truncation
    if not GEMINI_API_KEY:
        return simple_truncate_summary(content, max_length)
    
    try:
        # Create prompt for summary generation
        summary_prompt = f"""Summarize the following content in {max_length} characters or less. Focus on the main claims, key facts, and essential information. Make it clear and concise:
CONTENT:
"{content[:2000]}"
Provide ONLY the summary text, no additional formatting or explanations."""
        payload = {
            "contents": [{"parts": [{"text": summary_prompt}]}],
            "generationConfig": {
                "temperature": 0.3,
                "topK": 40,
                "topP": 0.95,
                "maxOutputTokens": 100,
                "stopSequences": []
            },
            "safetySettings": [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"}
            ]
        }
        session = requests.Session()
        retries = Retry(total=2, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
        session.mount("https://", HTTPAdapter(max_retries=retries))
        
        response = session.post(
            f"{GEMINI_API_URL}?key={GEMINI_API_KEY}",
            json=payload,
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            if 'candidates' in result and len(result['candidates']) > 0:
                candidate = result['candidates'][0]
                if 'content' in candidate and 'parts' in candidate['content']:
                    summary = candidate['content']['parts'][0]['text'].strip()
                    
                    # Ensure summary doesn't exceed max_length
                    if len(summary) > max_length:
                        summary = summary[:max_length-3] + "..."
                    
                    return summary
        
        # If API call fails, fall back to simple truncation
        logger.warning("Gemini summary generation failed, using fallback")
        return simple_truncate_summary(content, max_length)
        
    except Exception as e:
        logger.warning(f"Error generating summary with Gemini: {str(e)}")
        return simple_truncate_summary(content, max_length)

def simple_truncate_summary(content, max_length=200):
    """Fallback summary function with smart sentence boundary detection"""
    if not content or len(content) <= max_length:
        return content
    
    # Try to find last complete sentence within reasonable range
    truncated = content[:max_length]
    last_period = truncated.rfind('.')
    
    # If we can find a sentence end in the last 40% of the truncated text
    if last_period > max_length * 0.6:
        return truncated[:last_period + 1]
    else:
        return truncated.rstrip() + "..."

# UPDATED: Function to check if content already exists in database
def check_existing_analysis(content_hash):
    """Check if we already have analysis for this content"""
    if not content_hash:
        return None
    
    logger.info(f"Looking for cached analysis with hash: {content_hash}")
    
    try:
        # Query DynamoDB for existing analysis with this content hash
        response = table.scan(
            FilterExpression='#meta.#content_hash = :hash',
            ExpressionAttributeNames={
                '#meta': 'Meta',
                '#content_hash': 'content_hash'
            },
            ExpressionAttributeValues={':hash': content_hash},
            Limit=1
        )
        
        logger.info(f"DynamoDB scan returned {len(response['Items'])} items")
        
        if response['Items']:
            existing_item = response['Items'][0]
            logger.info(f"Found existing cached analysis for content hash: {content_hash}")
            return existing_item
        
        logger.info(f"No cached analysis found for hash: {content_hash}")
        return None
        
    except Exception as e:
        logger.error(f"Error checking existing analysis: {str(e)}")
        return None

def is_url(text):
    """Check if the input text is a URL"""
    return text.strip().startswith(('http://', 'https://'))

# Helper: URL Content Fetching
def fetch_url_content(url):
    """Fetch and extract text content from a URL"""
    try:
        session = requests.Session()
        retries = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
        session.mount("https://", HTTPAdapter(max_retries=retries))
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        response = session.get(url, headers=headers, timeout=15)
        
        if response.status_code != 200:
            logger.error(f"Failed to fetch URL {url}: HTTP {response.status_code}")
            return None
        
        # Parse HTML and extract text from multiple elements
        soup = BeautifulSoup(response.text, "html.parser")
        
        # Remove unwanted elements
        for element in soup(["script", "style", "nav", "header", "footer", "aside", "ad", "advertisement"]):
            element.decompose()
        
        # Try multiple extraction strategies
        content = ""
        
        # Strategy 1: Look for main content areas
        main_selectors = ["article", "main", ".content", ".article-content", ".post-content", "[role='main']"]
        for selector in main_selectors:
            main_content = soup.select(selector)
            if main_content:
                content = " ".join(element.get_text().strip() for element in main_content)
                if len(content) > 100:  # If we found substantial content
                    break
        
        # Strategy 2: If no main content found, use paragraphs and divs
        if not content or len(content) < 100:
            elements = soup.find_all(["p", "div", "h1", "h2", "h3", "li"])
            content = " ".join(element.get_text().strip() for element in elements if len(element.get_text().strip()) > 20)
        
        # Strategy 3: Last resort - get all text
        if not content or len(content) < 50:
            content = soup.get_text()
        
        # Clean up the content
        content = " ".join(content.split())  # Remove extra whitespace
        
        if not content or len(content) < 50:
            logger.warning(f"Insufficient content extracted from URL {url}")
            return None
        
        logger.info(f"Extracted {len(content)} characters from {url}")
        return content[:20000]  # Increased limit for better context

    except Exception as e:
        logger.error(f"Error fetching URL {url}: {str(e)}")
        return None

# UPDATED: Modified educational tag function for new assessment categories
def educational_tag_from_ai_assessment(ai_assessment, credibility_score):
    """Convert AI assessment to educational tags"""
    if not ai_assessment:
        return "Undetected"
    
    assessment = ai_assessment.lower()
    
    if "true" in assessment or assessment == "true":
        return "True"
    elif "false" in assessment or assessment == "false":
        return "False"
    elif "misleading" in assessment:
        return "Misleading"
    elif "uncertain" in assessment:
        return "Uncertain"
    else:
        return "Undetected"

# Helper: keyword extraction
def extract_keywords_simple(text, max_terms=6):
    if not text:
        return []
    stop_words = set(["the", "and", "a", "an", "in", "on", "at", "to", "for", "of", "with", "by", "is", "are", "was", "were"])
    tokens = [t.strip(".,:;\"'()[]{}").lower() for t in text.split()]
    tokens = [t for t in tokens if len(t) > 3 and t not in stop_words]
    seen = set()
    keywords = []
    for t in tokens:
        if t in seen:
            continue
        seen.add(t)
        keywords.append(t)
        if len(keywords) >= max_terms:
            break
    return keywords

# UPDATED: Modified Gemini prompt for X/10 format with enhanced opinion detection
def create_comprehensive_gemini_prompt(content, source_url=""):
    """Create prompt for Gemini to analyze content and source"""
    source_context = f"\n\nSOURCE URL/REFERENCE: {source_url}" if source_url else "\nSOURCE: Not provided"
    truncated_note = "\n\nNOTE: Content truncated to fit prompt limits." if len(content) > 4000 else ""
    truncated_content = content[:4000] + "..." if len(content) > 4000 else content
    
    prompt = f"""You are a professional fact-checker. Analyze the CONTENT for accuracy and credibility. IMPORTANT: If the content is primarily opinions, predictions, subjective statements, or personal views that cannot be verified with credible sources, classify it as "Uncertain".

CONTENT TO ANALYZE:
"{truncated_content}"
{source_context}{truncated_note}

ANALYSIS TASKS:
1. CONTENT TYPE IDENTIFICATION:
   - Determine if content contains verifiable factual claims OR primarily opinions/subjective statements
   - Look for opinion indicators: "I think", "I believe", "in my opinion", "personally", "should", "must", "best", "worst"
   - Identify predictions, future forecasts, or speculative statements without solid evidence
   - Check for subjective evaluations that cannot be fact-checked

2. CONTENT FACT-CHECKING (for factual claims only):
   - Identify specific factual claims that can be verified with credible sources
   - Assess accuracy and evidence quality
   - Check for logical consistency and bias
   - Look for misinformation patterns
   - Find reputable sources that verify or contradict claims

3. ASSESSMENT CATEGORIES (use exactly one):
   - **True**: Factual claims that are accurate and verified by credible sources (Score: 8/10 to 10/10)
   - **False**: Factual claims that are fabricated or clearly incorrect, no credible evidence (Score: 0/10)
   - **Misleading**: Factual claims that mix facts with distortions or context manipulation (Score: 4/10 to 7/10)
   - **Uncertain**: Opinions, subjective statements, predictions, personal views, OR factual claims with insufficient credible sources for verification (Score: 2/10 to 5/10)
   - **Undetected**: Cannot analyze or classify due to technical issues (Score: 0/10)

IMPORTANT RULES:
- If content is primarily opinions, personal views, subjective evaluations, or predictions → classify as "Uncertain"
- If factual claims cannot be verified with credible sources → classify as "Uncertain" 
- If content mixes opinions with facts, focus on the verifiable factual claims for assessment
- Only use "True" or "False" for clearly verifiable factual statements with strong evidence

RESPOND WITH ONLY THIS JSON FORMAT:
{{
    "overall_assessment": "True|False|Misleading|Uncertain|Undetected",
    "credibility_score": "X/10",
    "reasoning": "Detailed explanation including whether content is factual or opinion-based and specific claims analyzed",
    "content_concerns": ["concern1", "concern2"],
    "verifiable_claims": ["claim1", "claim2"],
    "evidence_quality": "Strong|Moderate|Weak|Insufficient|None",
    "discovered_sources": ["URL1 that you found during fact-checking", "URL2 that you found"],
    "credibility_explanation": "Why this score was assigned based on the assessment category and content type (factual vs opinion)"
}}

CRITICAL: Classify as "Uncertain" if content is primarily opinions, subjective statements, predictions, or if factual claims lack sufficient credible sources for verification."""
    
    return prompt

def call_gemini_comprehensive_check(content, source_url=""):
    """Call Gemini API with retries"""
    if not GEMINI_API_KEY or not content:
        logger.warning("Missing GEMINI_API_KEY or content")
        return None
    
    try:
        prompt = create_comprehensive_gemini_prompt(content, source_url)
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "topK": 40,
                "topP": 0.95,
                "maxOutputTokens": 1200,
                "stopSequences": []
            },
            "safetySettings": [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"}
            ]
        }

        session = requests.Session()
        retries = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
        session.mount("https://", HTTPAdapter(max_retries=retries))
        
        response = session.post(
            f"{GEMINI_API_URL}?key={GEMINI_API_KEY}",
            json=payload,
            timeout=45
        )
        
        if response.status_code != 200:
            logger.error(f"Gemini API error: HTTP {response.status_code} - {response.text}")
            return None
        
        result = response.json()
        
        if 'candidates' in result and len(result['candidates']) > 0:
            candidate = result['candidates'][0]
            if 'content' in candidate and 'parts' in candidate['content']:
                ai_response = candidate['content']['parts'][0]['text']
                clean_response = ai_response.strip().replace('```json', '').replace('```', '')
                try:
                    assessment = json.loads(clean_response)
                    if validate_comprehensive_response(assessment):
                        return assessment
                    else:
                        logger.warning("Invalid Gemini response structure")
                        return None
                except json.JSONDecodeError as e:
                    logger.error(f"JSON decode error: {e}")
                    return None
        
        return None

    except Exception as e:
        logger.error(f"Gemini API error: {str(e)}")
        return None

# UPDATED: Modified validation for X/10 string format
def validate_comprehensive_response(response):
    """Validate Gemini response structure"""
    if not isinstance(response, dict):
        return False
    
    required_fields = [
        "overall_assessment", "credibility_score", "reasoning", 
        "content_concerns", "verifiable_claims", "evidence_quality", 
        "discovered_sources", "credibility_explanation"
    ]
    if not all(field in response for field in required_fields):
        return False
    
    # UPDATED: New valid assessments
    valid_assessments = ["True", "False", "Misleading", "Uncertain", "Undetected"]
    if response.get("overall_assessment") not in valid_assessments:
        return False
    
    # UPDATED: Credibility score validation for "X/10" string format
    credibility_score = response.get("credibility_score", "")
    if not isinstance(credibility_score, str) or not credibility_score.endswith("/10"):
        return False
    
    try:
        score_num = int(credibility_score.split("/")[0])
        if not (0 <= score_num <= 10):
            return False
    except (ValueError, IndexError):
        return False
    
    if not all(isinstance(response.get(field, []), list) for field in ["content_concerns", "verifiable_claims", "discovered_sources"]):
        return False
    
    return True

# Safety Filters
def apply_safety_filters(gemini_result, content):
    """Apply safety filters for high-stakes content"""
    if not gemini_result:
        return gemini_result
    
    content_lower = content.lower()
    safety_adjustments = []
    
    high_stakes = {
        "medical": ["vaccine side effects", "cure for cancer", "covid treatment", "health scam"],
        "political": ["election fraud", "vote rigging", "government conspiracy"],
        "financial": ["guaranteed profit", "crypto scam", "investment opportunity"],
        "emergency": ["breaking news danger", "urgent evacuation", "imminent death"]
    }
    
    for category, patterns in high_stakes.items():
        if any(pattern in content_lower for pattern in patterns):
            safety_adjustments.append(f"{category.title()} content - professional verification recommended")
    
    if safety_adjustments:
        gemini_result["safety_notes"] = safety_adjustments
    
    return gemini_result

# UPDATED: Modified fallback analysis for X/10 format
def simple_fallback_analysis(content, source):
    """Basic analysis when Gemini is unavailable"""
    red_flags = [
        "you won't believe", "doctors hate this", "secret they don't want",
        "click here now", "limited time offer", "government doesn't want you to know"
    ]
    
    content_lower = content.lower()
    flag_count = sum(1 for flag in red_flags if flag in content_lower)
    
    if flag_count >= 2:
        return {
            "category": "Suspicious Content",
            "explanation": f"Contains {flag_count} red flag patterns commonly found in misinformation",
            "educational_tag": "Uncertain",
            "credibility_score": "3/10",  # UPDATED: Use X/10 format
            "method": "Pattern Recognition Fallback",
            "overall_assessment": "Uncertain"
        }
    
    return {
        "category": "Analysis Unavailable",
        "explanation": "Gemini AI service unavailable - manual review required",
        "educational_tag": "Undetected", 
        "credibility_score": "0/10",  # UPDATED: Use 0/10 for undetected
        "method": "Service Unavailable",
        "overall_assessment": "Undetected"
    }

# UPDATED: Core analysis function with X/10 format
def analyze_with_gemini_comprehensive(content, source=""):
    """Analyze content and source credibility with Gemini"""
    if not content:
        return {
            "category": "Cannot Detect",
            "explanation": "No content provided",
            "educational_tag": "Undetected",
            "credibility_score": "0/10",  # UPDATED: Use X/10 format
            "method": "No Content"
        }

    if GEMINI_API_KEY:
        gemini_result = call_gemini_comprehensive_check(content, source)
        if gemini_result:
            gemini_result = apply_safety_filters(gemini_result, content)
            assessment = gemini_result.get("overall_assessment", "Undetected")
            credibility_score = gemini_result.get("credibility_score", "0/10")  # UPDATED: Default to 0/10
            reasoning = gemini_result.get("reasoning", "Comprehensive analysis completed")
            
            # UPDATED: New category logic based on assessment types
            if assessment == "True":
                category = "Highly Credible"
            elif assessment == "False":
                category = "Fabricated/False"
            elif assessment == "Misleading":
                category = "Misleading Content"
            elif assessment == "Uncertain":
                category = "Uncertain"
            else:
                category = "Undetected"
            
            return {
                "category": category,
                "explanation": f"Analysis: {reasoning}",
                "educational_tag": educational_tag_from_ai_assessment(assessment, credibility_score),
                "credibility_score": credibility_score,  # UPDATED: Keep X/10 format
                "overall_assessment": assessment,
                "credibility_explanation": gemini_result.get("credibility_explanation", "AI analysis"),
                "content_concerns": gemini_result.get("content_concerns", []),
                "evidence_quality": gemini_result.get("evidence_quality", "Unknown"),
                "discovered_sources": gemini_result.get("discovered_sources", []),  # UPDATED: New field
                "safety_notes": gemini_result.get("safety_notes", []),
                "method": "Gemini Analysis"
            }
    
    return simple_fallback_analysis(content, source)

# UPDATED: Simplified validation for single input field
def validate_payload(payload):
    """Validate payload for required fields"""
    if not payload.get("input") and not payload.get("text") and not payload.get("statement"):
        return False, "Input field is required (use 'input', 'text', or 'statement')"
    return True, "ok"

# UPDATED: Handle "N/A" in convert_floats_to_decimal
def convert_floats_to_decimal(obj):
    """Convert float values to Decimal for DynamoDB compatibility"""
    if isinstance(obj, dict):
        return {k: convert_floats_to_decimal(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_floats_to_decimal(v) for v in obj]
    elif isinstance(obj, float):
        return Decimal(str(obj))
    elif obj == "N/A":  # UPDATED: Handle N/A values
        return "N/A"
    else:
        return obj

# UPDATED: Modified Lambda handler for single input processing with CORS headers
def lambda_handler(event, context):
    # UPDATED: Handle preflight OPTIONS request
    if event.get('httpMethod') == 'OPTIONS':
        return {
            "statusCode": 200,
            "headers": CORS_HEADERS,
            "body": ""
        }
    
    # Validate environment
    if not GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY missing")
        return {
            "statusCode": 500,
            "headers": CORS_HEADERS,
            "body": json.dumps({"message": "GEMINI_API_KEY environment variable is missing"})
        }
    
    try:
        dynamodb.meta.client.describe_table(TableName=DDB_TABLE_NAME)
    except ClientError as e:
        if e.response['Error']['Code'] == 'ResourceNotFoundException':
            logger.error(f"DynamoDB table {DDB_TABLE_NAME} does not exist")
            return {
                "statusCode": 500,
                "headers": CORS_HEADERS,
                "body": json.dumps({"message": f"DynamoDB table {DDB_TABLE_NAME} does not exist"})
            }
        raise

    # UPDATED: Parse request for single input field
    payload = None
    if isinstance(event.get("body"), str):
        try:
            payload = json.loads(event.get("body"))
        except Exception:
            # UPDATED: Treat plain text as input
            payload = {"input": event.get("body", "")}
    else:
        payload = event.get("body") if isinstance(event.get("body"), dict) else event

    if not isinstance(payload, dict):
        return {
            "statusCode": 400,
            "headers": CORS_HEADERS,
            "body": json.dumps({"message": "Invalid event payload"})
        }

    # UPDATED: Extract single input field
    user_input = payload.get("input", "") or payload.get("text", "") or payload.get("statement", "")
    
    if not user_input:
        return {
            "statusCode": 400,
            "headers": CORS_HEADERS,
            "body": json.dumps({"message": "Input field is required"})
        }
    
    # UPDATED: Check word limit for text input (not URLs)
    if not is_url(user_input):
        word_count = count_words(user_input)
        if word_count > 1500:
            return {
                "statusCode": 400,
                "headers": CORS_HEADERS,
                "body": json.dumps({"message": "Input exceeds 1,500 words. Please submit shorter statements for better accuracy."})
            }
    
    # UPDATED: Handle URL vs text input
    content = ""
    source_url = ""
    
    if is_url(user_input):
        content = fetch_url_content(user_input)
        if not content:
            # UPDATED: Return specific message for inaccessible links
            return {
                "statusCode": 400,
                "headers": CORS_HEADERS,
                "body": json.dumps({"message": "The link cannot be accessed. Please copy the statement from the page and submit it as text instead."})
            }
        source_url = user_input
    else:
        content = user_input
        source_url = ""
    
    # UPDATED: Check for existing analysis first to speed up duplicate requests
    content_hash = create_content_hash(content)
    existing_analysis = check_existing_analysis(content_hash) if content_hash else None
    
    if existing_analysis:
        # Return existing analysis with new NewsID and timestamp
        new_news_id = f"FC{int(time.time())}"
        new_timestamp = str(int(time.time()))
        
        # Create new item with existing analysis but new IDs
        cached_item = {
            "NewsID": new_news_id,
            "Timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "Content": content,
            "Label": "User_Fact_Check",
            "Source": source_url,
            "Meta": existing_analysis["Meta"].copy()  # Use existing analysis
        }
        
        # Update the content_hash in meta to ensure consistency
        cached_item["Meta"]["content_hash"] = content_hash
        cached_item["Meta"]["cached_result"] = True
        
        # Save the cached result as new entry
        try:
            item_for_db = convert_floats_to_decimal(cached_item)
            table.put_item(Item=item_for_db)
        except Exception as e:
            logger.error(f"Error saving cached result: {str(e)}")
        
        return {
            "statusCode": 200,
            "headers": CORS_HEADERS,
            "body": json.dumps({
                "message": "Fact-check analysis completed (cached result)",
                "NewsID": cached_item["NewsID"],
                "AI_Service": "Google Gemini (Cached)",
                "Overall_Assessment": cached_item["Meta"].get("assessment", "Undetected"),
                "Credibility_Score": cached_item["Meta"].get("credibility_score", "0/10"),
                "Category": cached_item["Meta"].get("category", "Unknown"),
                "Discovered_Sources": cached_item["Meta"].get("discovered_sources", []),
                "Meta": cached_item["Meta"],
                "Cached": True
            })
        }

    # UPDATED: Create simplified item structure
    current_time = time.time()
    news_id = f"FC{int(current_time)}"  # FC = Fact Check + Unix timestamp
    readable_timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

    # Analyze with Gemini
    factcheck = analyze_with_gemini_comprehensive(content, source_url)
    
    # MODIFIED: Create item with new Meta structure to match your format and improved summary
    item = {
        "NewsID": news_id,
        "Timestamp": readable_timestamp,
        "Content": content,
        "Label": "User_Fact_Check",
        "Source": source_url,
        "Meta": {
            "summary": generate_intelligent_summary(content, 200),
            "keywords": extract_keywords_simple(content),
            "assessment": factcheck.get("overall_assessment", "Undetected"),
            "category": factcheck.get("category", "Unknown"),
            "credibility_score": factcheck.get("credibility_score", "0/10"),
            "explanation": factcheck.get("explanation", "No analysis available"),
            "credibility_explanation": factcheck.get("credibility_explanation", "AI analysis"),
            "content_concerns": factcheck.get("content_concerns", []),
            "evidence_quality": factcheck.get("evidence_quality", "Unknown"),
            "discovered_sources": factcheck.get("discovered_sources", []),
            "content_hash": content_hash,  # ADDED: Store content hash for future caching
            "cached_result": False  # ADDED: Mark as new analysis
        }
    }

    # Save to DynamoDB
    try:
        item_for_db = convert_floats_to_decimal(item)
        table.put_item(Item=item_for_db)
    except ClientError as e:
        logger.error(f"DynamoDB error: {str(e)}")
        return {
            "statusCode": 500,
            "headers": CORS_HEADERS,
            "body": json.dumps({"message": "Error storing data", "error": str(e)})
        }
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return {
            "statusCode": 500,
            "headers": CORS_HEADERS,
            "body": json.dumps({"message": "Unexpected error", "error": str(e)})
        }

    # UPDATED: Return response with new fields and CORS headers
    return {
        "statusCode": 200,
        "headers": CORS_HEADERS,
        "body": json.dumps({
            "message": "Fact-check analysis completed",
            "NewsID": item["NewsID"],
            "AI_Service": "Google Gemini",
            "Overall_Assessment": factcheck.get("overall_assessment", "Undetected"),
            "Credibility_Score": factcheck.get("credibility_score", "0/10"),  # UPDATED: X/10 format
            "Category": factcheck.get("category", "Unknown"),
            "Discovered_Sources": factcheck.get("discovered_sources", []),  # UPDATED: New field
            "Meta": item["Meta"],
            "Cached": False  # ADDED: Indicate this is a new analysis
        })
    }