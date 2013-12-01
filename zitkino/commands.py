# -*- coding: utf-8 -*-


import times
from flask.ext.script import Manager, Command

from . import log
from .scrapers import scrapers
from .services import pair, services
from . import __version__ as version
from .models import Cinema, Showtime, Film


class Version(Command):
    """Print version."""

    def run(self):
        print version


class SyncShowtimes(Command):
    """Sync showtimes."""

    def _save_showtime(self, showtime):
        if showtime.starts_at >= times.now():
            log.info('Scraping: %s', showtime)
            showtime.save()

    def _sync_cinema(self, cinema, showtimes):
        log.info('Scraping: %s', cinema.name)

        now = times.now()
        counter = 0

        try:
            for showtime in showtimes:
                self._save_showtime(showtime)
                counter += 1
        except:
            log.exception()
        else:
            query = Showtime.objects(cinema=cinema, scraped_at__lt=now)
            query.delete()
        finally:
            log.info('Scraping: created %d showtimes', counter)

    def run(self):
        for cinema_slug, scraper in scrapers.items():
            cinema = Cinema.objects(slug=cinema_slug).get()
            showtimes = scraper()
            if showtimes:
                self._sync_cinema(cinema, showtimes)


class SyncPairing(Command):
    """Find unpaired showtimes and try to find films for them."""

    def _get_showtimes(self):
        return Showtime.objects.filter(film_paired=None)

    def _pair_by_db(self, showtime):
        scraped_title = showtime.film_scraped.title_normalized
        scraped_year = showtime.film_scraped.year

        params = {'titles': scraped_title}
        if scraped_year is not None:
            params['year'] = scraped_year
        try:
            return Film.objects.get(**params)
        except Film.DoesNotExist:
            return None

    def _pair_by_services(self, showtime):
        log.info('Pairing: asking services')
        film = pair(showtime.film_scraped)
        if film:
            film.titles.append(showtime.film_scraped.title_normalized)
        return film

    def _pair(self, showtime):
        return self._pair_by_db(showtime) or self._pair_by_services(showtime)

    def run(self):
        for showtime in self._get_showtimes():
            log.info('Pairing: %s', showtime)

            film = self._pair(showtime)
            if film:
                film.save_overwrite()
                log.info('Pairing: found %s', film)
                showtime.film_paired = film
            else:
                showtime.film_paired = None
                log.info('Pairing: no match')
            showtime.save()


class SyncFullPairing(SyncPairing):
    """Try to pair all showtimes."""

    def _get_showtimes(self):
        return Showtime.objects.all()

    def _pair(self, showtime):
        return self._pair_by_services(showtime)


class SyncCleanup(Command):
    """Find redundant films and showtimes and delete them to keep
    the db lightweight.
    """

    def _delete_old_showtimes(self):
        query = Showtime.objects.filter(starts_at__lt=times.now())
        count = query.count()
        query.delete()
        log.info('Cleanup: deleted %d old showtimes.', count)

    def _delete_redundant_showtimes(self):
        count = 0
        for showtime in Showtime.objects.all():
            duplicates = Showtime.objects.filter(
                id__ne=showtime.id,
                cinema=showtime.cinema,
                starts_at=showtime.starts_at,
                film_scraped__title_main=showtime.film_scraped.title_main
            )
            count += duplicates.count()
            duplicates.delete()
        log.info('Cleanup: deleted %d redundant showtimes.', count)

    def _delete_redundant_films(self):
        for film in Film.objects.all():
            if not Showtime.objects.filter(film_paired=film).count():
                log.info('Cleanup: deleting redundant film %s.', film)
                film.delete()

    def run(self):
        self._delete_old_showtimes()
        self._delete_redundant_showtimes()
        self._delete_redundant_films()


class SyncUpdate(Command):
    """Find paired films and try to get newer/more complete data for them."""

    def _update(self, film):
        for service in services:
            log.info('Update: asking %s', service.name)
            try:
                film.sync(service.lookup_obj(film))
            except NotImplementedError:
                log.info('Update: not implemented')

    def run(self):
        for film in Film.objects.all():
            log.info('Update: %s', film)
            self._update(film)


class SyncAll(Command):
    """Sync all."""

    def run(self):
        SyncShowtimes().run()
        SyncPairing().run()
        SyncCleanup().run()
        SyncUpdate().run()


sync = Manager(usage="Run synchronizations.")
sync.add_command('showtimes', SyncShowtimes())
sync.add_command('pairing', SyncPairing())
sync.add_command('full-pairing', SyncFullPairing())
sync.add_command('cleanup', SyncCleanup())
sync.add_command('update', SyncUpdate())
sync.add_command('all', SyncAll())
