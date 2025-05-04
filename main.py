# Bandcamp web scraping
class BandcampScraper:
    @staticmethod
    async def get_release_info(url):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        html = await response.text()
                        soup = BeautifulSoup(html, 'html.parser')
                        
                        # Extract release info
                        title_elem = soup.find('h2', class_='trackTitle')
                        artist_elem = soup.find('span', itemprop='byArtist')
                        release_info = {
                            'artist': artist_elem.text.strip() if artist_elem else '',
                            'title': title_elem.text.strip() if title_elem else '',
                            'release_date': '',
                            'tracks': [],
                            'genres': [],
                            'image_url': '',
                            'url': url
                        }
                        
                        # Get cover art
                        image_elem = soup.find('div', class_='popupImage')
                        if image_elem and image_elem.find('img'):
                            release_info['image_url'] = image_elem.find('img')['src']
                        
                        # Get track list
                        tracks = soup.find_all('div', class_='track_row_view')
                        release_info['tracks'] = [track.find('span', class_='track-title').text.strip() for track in tracks if track.find('span', class_='track-title')]
                        
                        # Get genres from tags
                        tag_elements = soup.find_all('a', class_='tag')
                        release_info['genres'] = [tag.text.strip() for tag in tag_elements[:3]]
                        
                        # Get release date
                        album_info = soup.find('div', class_='albumInfo')
                        if album_info:
                            date_text = album_info.text
                            # Parse date from text like "released April 1, 2023"
                            import re
                            date_match = re.search(r'released\s+(\w+\s+\d+,\s+\d+)', date_text)
                            if date_match:
                                release_info['release_date'] = date_match.group(1)
                        
                        return release_info
                    else:
                        logger.error(f"Failed to fetch Bandcamp page: {response.status}")
                        return None
        except Exception as e:
            logger.error(f"Error scraping Bandcamp: {str(e)}")
            return None

# Create spotify_api instance
spotify_api = SpotifyAPI()
