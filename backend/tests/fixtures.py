"""Offline HTML Fixtures for AO3 Ingestion Testing."""

HTML_STANDARD_MULTI_CHAPTER = """<!DOCTYPE html>
<html>
<head><title>The Great Adventure - Chapter 1 - AO3</title></head>
<body>
<div id="workskin">
  <div class="preface group">
    <h2 class="title heading">The Great Adventure</h2>
    <h3 class="byline heading">by <a rel="author" href="/users/testauthor">TestAuthor</a></h3>
    <div class="notes module"><p>Work-level author notes before the story.</p></div>
    <div class="summary module"><p>Work-level summary that must be excluded.</p></div>
  </div>

  <dl class="work meta group">
    <dd class="fandom tags"><ul class="commas"><li><a class="tag" href="/tags/Anime">Anime</a></li></ul></dd>
    <dd class="character tags"><ul class="commas">
      <li><a class="tag" href="/tags/Hero">Hero</a></li>
      <li><a class="tag" href="/tags/Villain">Villain</a></li>
    </ul></dd>
    <dd class="relationship tags"><ul class="commas"><li><a class="tag" href="/tags/Hero*s*Villain">Hero/Villain</a></li></ul></dd>
    <dd class="language">English</dd>
    <dd class="chapters">1/5</dd>
  </dl>

  <select id="selected_id" name="chapter_id">
    <option value="987654" selected="selected">1. The Beginning</option>
    <option value="987655">2. The Middle</option>
    <option value="987656">3. The End</option>
  </select>

  <ul class="actions" role="navigation">
    <li class="chapter previous"></li>
    <li class="chapter next"><a href="/works/12345/chapters/987655">Next Chapter →</a></li>
  </ul>

  <div id="chapters">
    <div class="chapter preface group">
      <h3 class="title"><a href="/works/12345/chapters/987654">Chapter 1</a>: The Beginning</h3>
      <div class="summary module"><p>Chapter summary that should also be decomposed.</p></div>
      <div class="notes module"><p>Chapter beginning note to ignore.</p></div>
    </div>

    <!-- Exact story container -->
    <div class="userstuff module" role="article">
      <h3 class="landmark heading">Chapter Text</h3>
      <p>The morning light broke through the dense canopy of the enchanted forest.</p>
      <p>"Are you certain we are on the right path?" asked Hero, looking back over his shoulder.</p>
      <p>"Certainty is a luxury we cannot afford," replied Villain calmly.</p>
      <hr>
      <p>Later that evening, after the storm had cleared, they made camp near the riverbank.</p>
    </div>

    <div class="end notes module">
      <p>End notes from the author asking for comments and kudos.</p>
    </div>
  </div>
</div>
</body>
</html>
"""

HTML_ONESHOT_WORK = """<!DOCTYPE html>
<html>
<head><title>A Quiet Night - AO3</title></head>
<body>
<div id="workskin">
  <h2 class="title heading">A Quiet Night</h2>
  <h3 class="byline heading">by <a rel="author" href="/users/solo">SoloWriter</a></h3>
  <dl class="work meta group">
    <dd class="fandom tags"><ul class="commas"><li><a class="tag" href="/tags/Original">Original Work</a></li></ul></dd>
    <dd class="character tags"><ul class="commas"><li><a class="tag" href="/tags/Wanderer">Wanderer</a></li></ul></dd>
    <dd class="language">English</dd>
    <dd class="chapters">1/1</dd>
  </dl>

  <div class="userstuff">
    <p>Silence settled over the abandoned cottage on the hill.</p>
    <p>The hearth was cold, yet a strange ember glowed deep within the ashes.</p>
  </div>
</div>
</body>
</html>
"""

HTML_WITH_FORMATTING_AND_SCENE_BREAKS = """<!DOCTYPE html>
<html>
<body>
<div id="workskin">
  <h2 class="title heading">Formatting Test</h2>
  <div id="chapters">
    <div class="userstuff module" role="article">
      <p>This is <em>italic text</em> and this is <strong>bold text</strong> and nested <em><strong>bold italic</strong></em>.</p>
      <p>He said, “Wait—don’t leave yet…” with smart quotes and em—dash and ellipsis…</p>
      <p>Zero\u200bwidth\u200ccharacters\u200dand\ufeffsoft\u00adhyphens\u00a0here.</p>
      <p>   * * *   </p>
      <p>Legacy paragraph 1<br><br>Legacy paragraph 2 with <br> inline break.</p>
      <hr>
      <p>---</p>
      <p>Final closing thought after divider.</p>
    </div>
  </div>
</div>
</body>
</html>
"""

HTML_RESTRICTED_WORK = """<!DOCTYPE html>
<html>
<head><title>Restricted - AO3</title></head>
<body>
<div id="signin">
  <h2>Log In to Archive of Our Own</h2>
  <form action="/users/login" method="post">
    <p>This work is only available to registered users of the Archive.</p>
  </form>
</div>
</body>
</html>
"""

HTML_UNREVEALED_WORK = """<!DOCTYPE html>
<html>
<head><title>Mystery Work - AO3</title></head>
<body>
<div id="workskin">
  <p class="notice">This is a mystery work, part of an unrevealed collection. It will become available after reveals.</p>
</div>
</body>
</html>
"""

