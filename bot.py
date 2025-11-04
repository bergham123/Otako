import feedparser
import telegram
import asyncio
import os
import logging
from bs4 import BeautifulSoup
from PIL import Image
from io import BytesIO
import requests
import json
from datetime import datetime
import time

# --- CONFIGURATION ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Crunchyroll News
CRUNCHYROLL_RSS_URL = "https://cr-news-api-service.prd.crunchyrollsvc.com/v1/ar-SA/rss"
CRUNCHYROLL_SENT_FILE = "sent_posts.txt"
CRUNCHYROLL_ARTICLES_FILE = "crunchyroll_articles.json"

# YouTube
CHANNEL_ID = "UC1WGYjPeHHc_3nRXqbW3OcQ"
YOUTUBE_RSS_URL = f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"
YOUTUBE_SENT_FILE = "sent_videos.txt"
YOUTUBE_ARTICLES_FILE = "youtube_videos.json"

# How many recent entries to check from the RSS feed
MAX_ENTRIES_TO_CHECK = 5  # Adjust this number based on your needs

# Logo settings
LOGO_PATH = "logo.png"  # Path to your logo file
LOGO_MIN_WIDTH_RATIO = 0.10  # 10% of image width
LOGO_MAX_WIDTH_RATIO = 0.20  # 20% of image width
LOGO_MARGIN = 10  # px margin from top-right

# --- Headers to bypass 403 ---
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ar-SA,ar;q=0.9,en;q=0.8",
    "Accept": "application/xml,application/xhtml+xml,text/html;q=0.9,image/webp,*/*;q=0.8",
    "Connection": "keep-alive"
}

# --- LOGGING ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# --- HELPER FUNCTIONS ---

def load_all_sent_posts(sent_file):
    """Loads all sent post IDs from a file into a set for quick lookup."""
    if not os.path.exists(sent_file):
        # Create the file if it doesn't exist
        with open(sent_file, "w", encoding="utf-8") as f:
            pass
        return set()
        
    try:
        with open(sent_file, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f if line.strip())
    except Exception as e:
        logging.error(f"Error reading {sent_file}: {e}")
        return set()

def save_sent_post(post_id, sent_file):
    """Prepends a new post ID to the file."""
    try:
        # Read all existing content
        content = ""
        if os.path.exists(sent_file):
            with open(sent_file, "r", encoding="utf-8") as f:
                content = f.read()
        
        # Write new ID first, then existing content
        with open(sent_file, "w", encoding="utf-8") as f:
            f.write(str(post_id) + "\n")
            if content:
                f.write(content)
    except Exception as e:
        logging.error(f"Error writing to {sent_file}: {e}")

def load_articles(articles_file):
    """Loads articles from a JSON file."""
    if not os.path.exists(articles_file):
        # Create the file with an empty list if it doesn't exist
        with open(articles_file, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=2)
        return []
        
    try:
        with open(articles_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Error reading {articles_file}: {e}")
        return []

def save_article(article_data, articles_file):
    """Saves article data to a JSON file."""
    try:
        # Load existing articles
        articles = load_articles(articles_file)
        
        # Check if article already exists
        for article in articles:
            if article.get("id") == article_data.get("id"):
                return  # Skip if already exists
        
        # Add new article at the beginning of the list
        articles.insert(0, article_data)
        
        # Save back to file
        with open(articles_file, "w", encoding="utf-8") as f:
            json.dump(articles, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Error writing to {articles_file}: {e}")

def shorten_text(text, words=25):
    """Shortens text to a specific number of words."""
    if not text:
        return ""
    w = text.split()
    short = ' '.join(w[:words])
    return short + "..." if len(w) > words else short

def clean_description(description_html):
    """Cleans HTML from description and returns plain text."""
    if not description_html:
        return ""
    soup = BeautifulSoup(description_html, "html.parser")
    return soup.get_text().strip()

def add_logo_to_image(image_url):
    """Adds a logo to the image and returns the modified image."""
    try:
        # Check if logo file exists
        if not os.path.exists(LOGO_PATH):
            return None
            
        response = requests.get(image_url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        post_image = Image.open(BytesIO(response.content)).convert("RGBA")
        logo = Image.open(LOGO_PATH).convert("RGBA")

        pw, ph = post_image.size

        # Determine logo width based on image size
        lw = int(pw * LOGO_MIN_WIDTH_RATIO) if pw < 600 else int(pw * LOGO_MAX_WIDTH_RATIO)
        logo_ratio = lw / logo.width
        lh = int(logo.height * logo_ratio)
        logo = logo.resize((lw, lh), Image.LANCZOS)

        # Paste logo top-right with margin
        position = (pw - lw - LOGO_MARGIN, LOGO_MARGIN)
        post_image.paste(logo, position, logo)

        output = BytesIO()
        post_image.save(output, format="PNG")
        output.seek(0)
        return output
    except Exception as e:
        logging.error(f"Error adding logo: {e}")
        return None

# --- CRUNCHYROLL NEWS LOGIC ---

async def check_and_send_crunchyroll_news(bot):
    """Checks the Crunchyroll RSS feed for new entries and sends them to Telegram."""
    start_time = time.time()
    sent_posts = load_all_sent_posts(CRUNCHYROLL_SENT_FILE)
    new_posts_found = 0

    try:
        news_feed = feedparser.parse(CRUNCHYROLL_RSS_URL)
        
        if not news_feed.entries:
            logging.warning("No entries found in the Crunchyroll RSS feed.")
            return

        # Check the latest entries (up to MAX_ENTRIES_TO_CHECK)
        for i, entry in enumerate(news_feed.entries[:MAX_ENTRIES_TO_CHECK]):
            post_id = entry.id
            title = entry.title

            # Check if this post has already been sent
            if post_id in sent_posts:
                logging.info(f"Crunchyroll post '{title}' was already sent.")
                continue

            logging.info(f"New Crunchyroll post found: '{title}'")
            new_posts_found += 1

            # Extract image URL
            image_url = None
            if hasattr(entry, 'media_thumbnail') and entry.media_thumbnail:
                image_url = entry.media_thumbnail[0]['url']
            
            # If no image in media_thumbnail, try to extract from description
            if not image_url and hasattr(entry, 'description'):
                desc_soup = BeautifulSoup(entry.description, "html.parser")
                img_tag = desc_soup.find("img")
                if img_tag and img_tag.has_attr("src"):
                    image_url = img_tag["src"]

            # Get full description
            full_description = clean_description(entry.description)
            
            # Get publication date
            pub_date = None
            if hasattr(entry, 'published'):
                pub_date = entry.published
            elif hasattr(entry, 'updated'):
                pub_date = entry.updated
            
            # Get URL
            url = entry.link if hasattr(entry, 'link') else None
            
            # Create article data
            article_data = {
                "id": post_id,
                "title": title,
                "url": url,
                "image_url": image_url,
                "description": full_description,
                "date": pub_date,
                "category": "Crunchyroll News",
                "saved_at": datetime.now().isoformat()
            }
            
            # Save article to JSON
            save_article(article_data, CRUNCHYROLL_ARTICLES_FILE)
            
            # Create short description for Telegram
            short_desc = shorten_text(full_description, words=25)
            caption = f"📰 *{title}*\n\n{short_desc}"
            
            try:
                if image_url:
                    # Try to add logo to image
                    image_with_logo = add_logo_to_image(image_url)
                    if image_with_logo:
                        await bot.send_photo(
                            chat_id=TELEGRAM_CHAT_ID, 
                            photo=image_with_logo, 
                            caption=caption, 
                            parse_mode='Markdown'
                        )
                    else:
                        # If logo addition failed, send original image
                        await bot.send_photo(
                            chat_id=TELEGRAM_CHAT_ID, 
                            photo=image_url, 
                            caption=caption, 
                            parse_mode='Markdown'
                        )
                else:
                    await bot.send_message(
                        chat_id=TELEGRAM_CHAT_ID, 
                        text=caption, 
                        parse_mode='Markdown'
                    )
                
                # Save the post ID to sent_posts.txt
                save_sent_post(post_id, CRUNCHYROLL_SENT_FILE)
                sent_posts.add(post_id)  # Add to our set to avoid duplicates in this run
                
                # Add a small delay between posts to avoid rate limiting
                if i < MAX_ENTRIES_TO_CHECK - 1:
                    await asyncio.sleep(2)
                
            except Exception as e:
                logging.error(f"Failed to send Crunchyroll message to Telegram: {e}")

    except Exception as e:
        logging.error(f"Error parsing Crunchyroll RSS feed: {e}")
    
    elapsed_time = time.time() - start_time
    logging.info(f"Crunchyroll check completed in {elapsed_time:.2f} seconds. Found {new_posts_found} new posts.")

# --- YOUTUBE VIDEO LOGIC ---

async def check_and_send_youtube_video(bot):
    """Checks the YouTube RSS feed for new videos and sends them to Telegram."""
    start_time = time.time()
    sent_videos = load_all_sent_posts(YOUTUBE_SENT_FILE)
    new_videos_found = 0

    try:
        video_feed = feedparser.parse(YOUTUBE_RSS_URL)
        
        if not video_feed.entries:
            logging.warning("No entries found in the YouTube RSS feed.")
            return

        # Check the latest entries (up to MAX_ENTRIES_TO_CHECK)
        for i, entry in enumerate(video_feed.entries[:MAX_ENTRIES_TO_CHECK]):
            video_id = entry.yt_videoid
            title = entry.title

            # Check if this video has already been sent
            if video_id in sent_videos:
                logging.info(f"YouTube video '{title}' was already sent.")
                continue

            logging.info(f"New YouTube video found: '{title}'")
            new_videos_found += 1

            # Extract thumbnail URL
            thumbnail_url = entry.media_thumbnail[0]['url'] if hasattr(entry, 'media_thumbnail') and entry.media_thumbnail else None
            
            # Extract video URL
            video_url = entry.link
            
            # Extract description
            full_description = clean_description(entry.description)
            
            # Get publication date
            pub_date = None
            if hasattr(entry, 'published'):
                pub_date = entry.published
            elif hasattr(entry, 'updated'):
                pub_date = entry.updated
            
            # Create video data
            video_data = {
                "id": video_id,
                "title": title,
                "url": video_url,
                "image_url": thumbnail_url,
                "description": full_description,
                "date": pub_date,
                "category": "YouTube Video",
                "saved_at": datetime.now().isoformat()
            }
            
            # Save video to JSON
            save_article(video_data, YOUTUBE_ARTICLES_FILE)
            
            # Create short description for Telegram
            short_desc = shorten_text(full_description, words=25)
            
            # Create caption
            caption = f"🎬 *{title}*\n\n{short_desc}\n\n[Watch on YouTube]({video_url})"
            
            try:
                if thumbnail_url:
                    # Try to add logo to thumbnail
                    image_with_logo = add_logo_to_image(thumbnail_url)
                    if image_with_logo:
                        await bot.send_photo(
                            chat_id=TELEGRAM_CHAT_ID, 
                            photo=image_with_logo, 
                            caption=caption, 
                            parse_mode='Markdown'
                        )
                    else:
                        # If logo addition failed, send original thumbnail
                        await bot.send_photo(
                            chat_id=TELEGRAM_CHAT_ID, 
                            photo=thumbnail_url, 
                            caption=caption, 
                            parse_mode='Markdown'
                        )
                else:
                    await bot.send_message(
                        chat_id=TELEGRAM_CHAT_ID, 
                        text=caption, 
                        parse_mode='Markdown'
                    )
                
                # Save the video ID to sent_videos.txt
                save_sent_post(video_id, YOUTUBE_SENT_FILE)
                sent_videos.add(video_id)  # Add to our set to avoid duplicates in this run
                
                # Add a small delay between posts to avoid rate limiting
                if i < MAX_ENTRIES_TO_CHECK - 1:
                    await asyncio.sleep(2)
                
            except Exception as e:
                logging.error(f"Failed to send YouTube message to Telegram: {e}")

    except Exception as e:
        logging.error(f"Error parsing YouTube RSS feed: {e}")
    
    elapsed_time = time.time() - start_time
    logging.info(f"YouTube check completed in {elapsed_time:.2f} seconds. Found {new_videos_found} new videos.")

# --- MAIN LOGIC ---

async def check_and_send_content():
    """Checks both RSS feeds for new content and sends them to Telegram if new."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logging.error("FATAL: TELEGRAM_TOKEN or TELEGRAM_CHAT_ID not set in environment variables.")
        return

    # Initialize bot inside the function
    bot = telegram.Bot(token=TELEGRAM_TOKEN)
    
    start_time = time.time()
    logging.info("===== Starting new bot run =====")
    
    # Check Crunchyroll news
    await check_and_send_crunchyroll_news(bot)
    
    # Check YouTube videos
    await check_and_send_youtube_video(bot)
    
    elapsed_time = time.time() - start_time
    logging.info(f"===== Bot run finished in {elapsed_time:.2f} seconds =====")


if __name__ == "__main__":
    asyncio.run(check_and_send_content())
