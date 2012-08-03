# -*- coding: utf-8 -*-


import re
import os
import requests
from subprocess import call as cmd
from dateutil import rrule
from datetime import datetime, date, time
from hashlib import sha1
import urllib
from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader, Markup


class Film(object):

    def __init__(self, cinema, date, title):
        self.cinema = cinema
        self.date = date
        self.title = title.upper()

    @property
    def hash(self):
        return sha1(
            str(self.cinema) +
            str(self.date) +
            self.title.encode('utf8')
        ).hexdigest()


class Driver(object):

    name = ''
    url = ''

    def __init__(self):
        self.films = []

    def download(self):
        if not self.url:
            classname = self.__class__.__name__
            raise ValueError('No URL for driver {0}.'.format(classname))

        response = requests.get(self.url)
        response.raise_for_status()
        return response.content

    def to_soup(self, html):
        return BeautifulSoup(re.sub(r'\s+', ' ', html))

    def parse(self, soup):
        return []

    def scrape(self):
        if not self.films:
            self.films = list(self.parse(self.to_soup(self.download())))
        return self.films

    def __unicode__(self):
        return self.name


class DobrakDriver(Driver):

    name = u'Dobrák'
    url = 'http://kinonadobraku.cz'
    web = 'http://kinonadobraku.cz'

    def parse(self, soup):
        dates = soup.select('#Platno .Datum_cas')
        names = soup.select('#Platno .Nazev')

        for date, name in zip(dates, names):
            date = re.sub(r'[^\d\-]', '', date['id'])
            film_date = datetime.strptime(date, '%Y-%m-%d')
            film_title = name.get_text().strip().upper()

            yield Film(self, film_date, film_title)


class StarobrnoDriver(Driver):

    name = u'Starobrno'
    url = 'http://www.kultura-brno.cz/cs/film/starobrno-letni-kino-na-dvore-mestskeho-divadla-brno'
    web = 'http://www.letnikinobrno.cz'

    def parse(self, soup):
        for row in soup.select('.content tr'):
            cells = row.select('td')

            if len(cells) == 3:
                # date
                date = cells[1].get_text()
                match = re.search(r'(\d+)\.(\d+)\.', date)
                if not match:
                    continue

                film_date = datetime(
                    int(2012),
                    int(match.group(2)),
                    int(match.group(1))
                )

                # title
                film_title = cells[2].get_text()
                if not film_title:
                    continue

                yield Film(self, film_date, film_title)


class ArtDriver(Driver):

    name = u'Art'
    url = 'http://www.kinoartbrno.cz'
    web = 'http://www.kinoartbrno.cz'

    def parse(self, soup):
        film_date = None

        for row in soup.select('#program_art tr'):
            if row.select('.datum'):
                match = re.search(r'(\d+)\. (\d+)\. (\d+)', row.get_text())
                film_date = datetime(
                    int(match.group(3)),
                    int(match.group(2)),
                    int(match.group(1))
                )

            else:
                match = re.search(r'^ *(\d+)\.(\d+) *(.+) *$', row.get_text())
                film_title = match.group(3).strip().upper()

                if film_title.lower() != 'kino nehraje':
                    yield Film(self, film_date, film_title)


class LucernaDriver(Driver):

    name = u'Lucerna'
    url = 'http://www.kinolucerna.info/index.php?option=com_content&view=article&id=37&Itemid=61'
    web = 'http://www.kinolucerna.info'

    re_range = re.compile(r'(\d+)\.(\d+)\.-(\d+)\.(\d+)\.')

    def __init__(self):
        self.today = datetime.now()
        super(LucernaDriver, self).__init__()

    def get_date_ranges(self, dates_text):
        today = self.today
        for match in self.re_range.finditer(dates_text):
            start_day = int(match.group(1))
            end_day = int(match.group(3))

            start_month = int(match.group(2))
            end_month = int(match.group(4))

            start_year = today.year if today.month <= start_month else (today.year + 1)
            end_year = today.year if today.month <= end_month else (today.year + 1)

            start = datetime(start_year, start_month, start_day)
            end = datetime(end_year, end_month, end_day)

            for day in rrule.rrule(rrule.DAILY, dtstart=start, until=end):
                yield day

    def get_standalone_dates(self, dates_text):
        today = self.today
        dates_text = self.re_range.sub('', dates_text)
        for match in re.finditer(r'(\d+)\.(\d+)\.', dates_text):
            month = int(match.group(2))
            year = today.year if today.month <= month else (today.year + 1)
            yield datetime(year, month, int(match.group(1)))

    def parse(self, soup):
        for row in soup.select('.contentpaneopen strong'):
            text = row.get_text().strip()
            match = re.search(r'\d+:\d+', text)
            if match:
                text = re.split(r'[\b\s]+(?=\d+\.)', text, maxsplit=1)

                film_title = text[0].strip().upper()
                dates_text = text[1]

                date_ranges = self.get_date_ranges(dates_text)
                standalone_dates = self.get_standalone_dates(dates_text)

                dates = list(date_ranges) + list(standalone_dates)
                for film_date in dates:
                    yield Film(self, film_date, film_title)


class Deployer(object):

    # https://devcenter.heroku.com/articles/read-only-filesystem
    temp_dir = './tmp'
    commit_message = 'automatic kino update'
    email = 'jan.javorek+kino@gmail.com'

    def __init__(self):
        self.username = os.environ.get('GITHUB_USERNAME')
        self.password = os.environ.get('GITHUB_PASSWORD')
        self._prepare()

    def _prepare(self):
        cmd(['rm', '-rf', self.temp_dir])
        cmd(['mkdir', self.temp_dir])

        print 'Cloning git repository.'
        cmd([
            'git', 'clone', '-b', 'gh-pages',
            'https://{0}:{1}@github.com/honzajavorek/blog.git'.format(self.username, self.password),
            self.temp_dir
        ])
        cmd(['git', 'config', 'user.name', 'Kino'], cwd=self.temp_dir)
        cmd(['git', 'config', 'user.email', self.email], cwd=self.temp_dir)

    def write(self, filename, contents):
        path = os.path.join(self.temp_dir, filename)
        print 'Writing file: ' + path
        with open(path, 'w') as f:
            f.write(contents)

    def deploy(self):
        print 'Commiting changes.'
        cmd(['git', 'add', '-A'], cwd=self.temp_dir)
        cmd(['git', 'commit', '-m', self.commit_message], cwd=self.temp_dir)

        print 'Pushing changes.'
        cmd(['git', 'push', 'origin', 'gh-pages'], cwd=self.temp_dir)

        cmd(['rm', '-rf', self.temp_dir])


class Debugger(object):

    debug_dir = './'
    browser_command = 'firefox'

    def __init__(self):
        self.files = []

    def write(self, filename, contents):
        debug_file = os.path.join(self.debug_dir, filename)
        with open(debug_file, 'w') as f:
            f.write(contents)
        self.files.append(debug_file)

    def show(self):
        for file in self.files:
            cmd([self.browser_command, file])


def urlencode_filter(s):
    if type(s) == 'Markup':
        s = s.unescape()
    s = s.encode('utf8')
    s = urllib.quote_plus(s)
    return Markup(s)


class Kino(object):

    drivers = (
        ArtDriver,
        DobrakDriver,
        LucernaDriver,
        StarobrnoDriver,
    )

    template_filters = {
        'format_date': lambda dt: dt.strftime('%d. %m.'),
        'format_date_ics': lambda dt: dt.strftime('%Y%m%d'),
        'format_timestamp_ics': lambda dt: dt.strftime('%Y%m%dT%H%M%SZ'),
        'urlencode': urlencode_filter,
    }

    templates_dir = '.'
    html_template_name = 'kino.html'
    ics_template_name = 'kino.ics'

    html_filename = 'kino.html'
    ics_filename = 'static/kino.ics'

    html_debug_filename = 'debug.html'
    ics_debug_filename = 'debug.ics'

    def __init__(self):
        self.today = datetime.combine(date.today(), time(0, 0))

    def setup_jinja_env(self):
        jinja_env = Environment(loader=FileSystemLoader(self.templates_dir))
        jinja_env.filters.update(self.template_filters)
        return jinja_env

    def scrape_films(self):
        films = []
        for driver in self.drivers:
            films += list(driver().scrape())
        sorted_films = sorted(films, key=lambda film: film.date)
        filtered_films = [f for f in sorted_films if f.date >= self.today]
        return filtered_films

    def render_template(self, filename, films):
        jinja_env = self.setup_jinja_env()
        template = jinja_env.get_template(filename)
        return template.render(
            films=films,
            today=self.today,
            now=datetime.now()
        ).encode('utf8')

    def run(self, debug=False):
        films = self.scrape_films()

        html = self.render_template(self.html_template_name, films)
        ics = self.render_template(self.ics_template_name, films)

        if debug:
            debugger = Debugger()
            debugger.write(self.html_debug_filename, html)
            debugger.write(self.ics_debug_filename, ics)
        else:
            deployer = Deployer()
            deployer.write(self.html_filename, html)
            deployer.write(self.ics_filename, ics)
            deployer.deploy()


if __name__ == '__main__':
    Kino().run()
