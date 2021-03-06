# -*- coding: utf-8 -*-

"""
Blog Handlers
"""
# standard library imports
import logging, os
import urllib, urllib2, hashlib, httplib2
import json
import datetime
import re

# related third party imports
import webapp2
from webapp2_extras import security
from webapp2_extras.auth import InvalidAuthIdError, InvalidPasswordError
from webapp2_extras.i18n import gettext as _
from webapp2_extras.appengine.auth.models import Unique
from web.basehandler import BaseHandler
from web.basehandler import user_required

# google shizzle
from google.appengine.api import taskqueue
from google.appengine.api import channel
from google.appengine.ext import db

# local application/library specific imports
import config
import web.forms as forms
import web.models.models as models
from lib import utils, httpagentparser, captcha
from lib.i18n import get_territory_from_ip
import bleach
import html5lib

# social login
from lib.github import github
from lib.twitter import twitter



##################
# Public Methods #
##################
class PublicBlogHandler(BaseHandler):
    def get(self):
        # load articles in from db and github, stuff them in an array
        date_format = "%a, %d %b %Y"
        articles = models.Article.get_all()
        blogposts = []
        
        # loop through all articles
        for article in articles:
            # if there's content on Github to serve
            try:
                gist_content = github.get_gist_content(article.gist_id)
            except:
                continue

            if gist_content:
                # sanitize javascript
                article_html = bleach.clean(gist_content, config.bleach_tags, config.bleach_attributes)
                article_title = bleach.clean(article.title)
                article_summary = bleach.clean(article.summary)

                # created and by whom
                created = article.created.strftime(date_format)
                owner_info = models.User.get_by_id(article.owner.id())
                
                # build entry
                entry = {
                    'created': created,
                    'article_id': article.key.id(),
                    'article_title': article_title,
                    'article_type': article.article_type, 
                    'article_html': article_html,
                    'article_summary': article_summary,
                    'article_slug': article.slug,
                    'article_owner': owner_info.username,
                    'article_host': self.request.host,
                }
                
                # append article if it's a guide, it's public, and not a draft            
                if article.article_type == 'post' and article.public and not article.draft:
                    blogposts.append(entry)
        
        # pack and stuff into template
        params = {'blogposts': blogposts}
        return self.render_template('blog/blog.html', **params)


class PublicGuideHandler(BaseHandler):
    def get(self):
        # load articles in from db and github, stuff them in an array
        date_format = "%a, %d %b %Y"
        articles = models.Article.get_all()
        guides = []
        
        # loop through all articles
        for article in articles:
            # if there's content on Github to serve
            gist_content = github.get_gist_content(article.gist_id)

            if gist_content:
                # sanitize javascript
                article_html = bleach.clean(gist_content, config.bleach_tags, config.bleach_attributes)
                article_title = bleach.clean(article.title)
                article_summary = bleach.clean(article.summary)

                # created and by whom
                created = article.created.strftime(date_format)
                owner_info = models.User.get_by_id(article.owner.id())

                # build entry
                entry = {
                    'created': created,
                    'article_id': article.key.id(),
                    'article_title': article_title,
                    'article_type': article.article_type, 
                    'article_html': article_html,
                    'article_summary': article_summary,
                    'article_slug': article.slug,
                    'article_owner': owner_info.username,
                    'article_host': self.request.host,
                }
                
                # append article if we have content, it's a guide, it's public, and not a draft
                if article.article_type == 'guide' and article.public and not article.draft:
                    guides.append(entry)

        # pack and stuff into template
        params = {'guides': guides}
        return self.render_template('blog/guide.html', **params)


# TODO: needs to be fixed to guarantee 10 items get spit out
class PublicBlogRSSHandler(BaseHandler):
   def get(self):
        # load articles in from db and github, stuff them in an array
        date_format = "%a, %d %b %Y"

        blog_title = "The %s Blog" % config.app_name
        epoch_start = datetime.datetime(1970, 1, 1)
        blog_last_updated = epoch_start

        entries = []
        
        # fetch our articles
        articles = models.Article.get_all()
        
        for article in articles[0:10]:
            gist_content = github.get_gist_content(article.gist_id)

            if gist_content:
                # sanitize
                article_html = bleach.clean(gist_content, config.bleach_tags, config.bleach_attributes)
                article_title = bleach.clean(article.title)
                article_summary = bleach.clean(article.summary)

                # look up owner
                owner_info = models.User.get_by_id(article.owner.id())

                if article.updated > blog_last_updated:
                    blog_last_updated = article.updated
                entry = {
                    'slug': article.slug,
                    'article_type': article.article_type,
                    'created': article.created,
                    'author_email': owner_info.email,
                    'author_username': owner_info.username,
                    'updated': article.updated,
                    'title': article_title, 
                    'summary': article_summary, 
                    'html': article_html,
                }

                if not article.draft:
                    entries.append(entry)

        # didn't get any matches in our loop
        date_format = "%a, %d %b %Y %H:%M:%S GMT"
        if blog_last_updated == epoch_start:
            blog_last_updated = datetime.datetime.utcnow().strftime(date_format) 
        else:
            blog_last_updated = blog_last_updated.strftime(date_format)

        params = {
            'blog_title': blog_title, 
            'blog_last_updated': blog_last_updated,
            'site_host': self.request.host,
            'entries': entries,
        }
        
        self.response.headers['Content-Type'] = 'application/xml'
        return self.render_template('blog/feed.xml', **params)


class BlogArticleSlugHandler(BaseHandler):
    # default to admin if you can't find article
    def get(self, username=config.admin_username, article_type='guides', slug = None):

        # look up our owner
        user = models.User.get_by_username(username)

        # look up the article
        try:
            article = models.Article.get_by_user_and_slug(user.key, slug)
            if not article:
                return self.render_template('errors/default_error.html')
        except:
            return self.render_template('errors/default_error.html')
            
        gist_content = github.get_gist_content(article.gist_id)

        # if there's content on Github to serve
        if gist_content:
            twitter_user = models.SocialUser.get_by_user_and_provider(user.key, 'twitter')
            owner_info = models.User.get_by_id(article.owner.id())
            
            # twitter widget stuff
            if twitter_user:
                twitter_username = twitter_user.screen_name
                twitter_widget_id = owner_info.twitter_widget_id
            else:
                twitter_username = config.app_twitter_username
                twitter_widget_id = config.app_twitter_widget_id

            # set nav menu pill
            if 'guide' in article_type:
                menu_choice = 'guides'
            else:
                menu_choice = 'blog'

            # load name
            if not owner_info.name:
                name = owner_info.username
            else:
                name = "%s %s" % (owner_info.name, owner_info.last_name)

            # load github use
            try:
                github_username = social_user.uid
            except:
                github_username = None

            # load articles in from db and github, stuff them in an array
            date_format = "%a, %d %b %Y"

            # sanitize javascript
            article_html = bleach.clean(gist_content, config.bleach_tags, config.bleach_attributes)
            article_title = bleach.clean(article.title)
            article_summary = bleach.clean(article.summary)

            # serve page if we have contents
            created = article.created.strftime(date_format)
            entry = {
                'created': created, 
                'article_id': article.key.id(), 
                'article_title': article_title,
                'article_html': article_html, 
                'article_slug': article.slug,
                'article_type': article.article_type,
                'article_owner': owner_info.username,
                'article_host': self.request.host,
                #'twitter_username': twitter_username,
            }
            # pack and stuff into template - duplicate on article_type TODO
            params = {
                'name': name,
                'github_username': github_username,
                'twitter_username': twitter_username,
                'twitter_widget_id': twitter_widget_id, 
                'article_type': article_type,
                'menu_choice': menu_choice, 
                'entry': entry,
            }
            return self.render_template('blog/blog_article_detail.html', **params)
        
        else:
            params = {}
            return self.render_template('errors/default_error.html', **params)


# shows a user's homepage - normal username value locally overwritten here
class BlogUserHandler(BaseHandler):
    def get(self, username = None):
        owner_info = models.User.get_by_username(username)

        if not owner_info:
            params = {}
            return self.redirect_to('home', **params)

        # load name
        if not owner_info.name:
            name = bleach.clean(owner_info.username)
        else:
            name = "%s %s" % (bleach.clean(owner_info.name), bleach.clean(owner_info.last_name))

        # find the browsed user's github username (used for follow button)
        owner_social_user = models.SocialUser.get_by_user_and_provider(owner_info.key, 'github')
        twitter_user = models.SocialUser.get_by_user_and_provider(owner_info.key, 'twitter')

        # load github usernames
        try:
            owner_github_username = owner_social_user.uid
            github_username = social_user.uid
        except:
            owner_github_username = None
            github_username = None

        # load bio
        if not owner_info.bio:
            bio = "User has not completed their bio."
        else:
            bio = bleach.clean(owner_info.bio)
        
        # load avatar
        if not owner_info.gravatar_url:
            gravatar_url = config.gravatar_url_stub
        else:
            gravatar_url = owner_info.gravatar_url

        # load articles in from db and github, stuff them in seperate arrays
        date_format = "%a, %d %b %Y"
        articles = models.Article.get_by_user(owner_info.key)
        blogposts = []
        guides = []
        
        # loop through all articles and build a list of both posts and articles
        for article in articles:
            # if there's content on Github to serve
            gist_content = github.get_gist_content(article.gist_id)
            
            if gist_content:
                # sanitize javascript            
                article_html = bleach.clean(gist_content, config.bleach_tags, config.bleach_attributes)
                article_title = bleach.clean(article.title)
                article_summary = bleach.clean(article.summary)

                created = article.created.strftime(date_format)
                entry = {
                    'created': created,
                    'article_id': article.key.id(), 
                    'article_title': article_title, 
                    'article_html': article_html,
                    'article_type': article.article_type,
                    'article_slug': article.slug,
                    'article_summary': article_summary,
                    'article_host': self.request.host,
                    'article_owner': bleach.clean(owner_info.username), # TODO: may not be correct if they have forked another user's article?
                }
                
                # we switch between adding to the two lists here
                # will need video handling eventually
                if gist_content and not article.draft:
                    if article.article_type == 'post':
                        blogposts.append(entry)
                    else:
                        guides.append(entry)

        # add extra single entry items, and then add the posts and guides seperately
        params = {
            'bio': bio,
            'name': name,
            'blog_username': username,
            'owner_github_username': owner_github_username,
            'google_plus_profile': owner_info.google_plus_profile,
            'gravatar_url': gravatar_url,
            'twitter_username': twitter_user.screen_name,
            'twitter_widget_id': owner_info.twitter_widget_id, 
            'blogposts': blogposts, 
            'guides': guides,
        }
        return self.render_template('blog/blog_user.html', **params)


##################
# Auth'd Methods #
##################
# class dealing with github actions for article including delete, draft update, forking
class BlogArticleActionsHandler(BaseHandler):
    @user_required
    def get(self, username=None, article_id = None):

        if not isinstance(article_id, (int, long)):
            return

        # pull the github token out of the social user db and then fork it
        user_info = models.User.get_by_id(long(self.user_id))
        social_user = models.SocialUser.get_by_user_and_provider(user_info.key, 'github')
        article = models.Article.get_by_id(long(article_id))

        # lame because we don't do anything if we fail here
        if article:
            gist = github.fork_gist(social_user.access_token, article.gist_id)
            # we have a new article on our hands after the fork - fetch the data and insert
            if gist:
                # prep the slug
                slug = utils.slugify(gist['title'])
                
                # stuff into entry
                article = models.Article(
                    title = gist['title'],
                    summary = gist['summary'],
                    created = datetime.datetime.fromtimestamp(gist['published']),
                    gist_id = gist['gist_id'],
                    owner = user_info.key,
                    slug = slug,
                    article_type = gist['article_type'],
                )
            
                # update db
                article.put()
        return

    @user_required
    def delete(self, username=None, article_id = None):
        # pull the github token out of the social user db
        user_info = models.User.get_by_id(long(self.user_id))
        social_user = models.SocialUser.get_by_user_and_provider(user_info.key, 'github')

        # delete the entry from the db
        article = models.Article.get_by_id(long(article_id))

        if article:
            article.key.delete()
            github.delete_user_gist(social_user.access_token, article.gist_id)
            self.add_message(_('Article successfully deleted!'), 'success')
        else:
            self.add_message(_('Article was not deleted.  Something went horribly wrong somewhere!'), 'warning')

        # use the channel to tell the browser we are done and reload
        channel_token = self.request.get('channel_token')
        channel.send_message(channel_token, 'reload')
        return

    # deal with draft or published status changes from slider
    @user_required
    def put(self, username=None, article_id = None):
        # pull the github token out of the social user db
        user_info = models.User.get_by_id(long(self.user_id))
        social_user = models.SocialUser.get_by_user_and_provider(user_info.key, 'github')

        # what's the draft status set to?
        # note: slider returns 'true' for published and 'false' for draft :(
        draft = self.request.get('draft')

        if draft == 'false':
            draft = False
        else:
            draft = True

        # update the entry
        article = models.Article.get_by_id(long(article_id))
        if article:
            article.draft = draft
            article.put()


class BlogArticleCreateHandler(BaseHandler):
    @user_required
    def get(self, username=None):
        # pull the github token out of the social user db
        user_info = models.User.get_by_id(long(self.user_id))
        social_user = models.SocialUser.get_by_user_and_provider(user_info.key, 'github')

        # what do we do if we don't have a token or association?  auth 'em!
        if not social_user:
            scope = 'gist'
            # drop a short lived cookie so we know where to come back to when we're done auth'ing
            utils.write_cookie(self, 'oauth_return_url', 'blog-article-create', '/', 15)
            github_helper = github.GithubAuth(scope)
            self.redirect( github_helper.get_authorize_url() )
            return
        else:
            params = {}
            return self.render_template('blog/blog_article_create.html', **params)

    @user_required
    def post(self, username=None):
        if not self.form.validate():
            return self.get()

        # pull the github token out of the social user db
        user_info = models.User.get_by_id(long(self.user_id))
        social_user = models.SocialUser.get_by_user_and_provider(user_info.key, 'github')
        
        # load values out of the form
        title = self.form.title.data.strip()
        summary = self.form.summary.data.strip()
        article_type = self.form.article_type.data.strip()
        
        # when written?
        published_epoch_gmt = int(datetime.datetime.now().strftime("%s"))

        # push the sample article to the user's gist on github (published in this context means date published)
        template_val = {
            "username": social_user.uid,
            "title": title,
            "summary": summary,
            "published": published_epoch_gmt,
            "type": article_type,
        }

        # build a dict of files we want to push into the gist
        files = {
            config.gist_manifest_name: self.jinja2.render_template("blog/gist_manifest_stub.txt", **template_val),
            config.gist_markdown_name: self.jinja2.render_template("blog/gist_markdown_stub.txt", **template_val)
        }

        # loop through them and add them to the other JSON values for github
        file_data = dict((filename, {'content': text}) for filename, text in files.items())
        data = json.dumps({'description': "%s for StackGeek" % title, 'public': True, 'files': file_data})

        # stuff it to github and then grab our gist_id
        gist = github.put_user_gist(social_user.access_token, data)
        gist_id = gist['id']

        # prep the slug
        slug = utils.slugify(title)
        
        # make sure it's not already in the database (unlikely)
        if not models.Article.get_by_user_and_gist_id(user_info.key, gist_id):
            # save the article in our database            
            article = models.Article(
                title = title,
                summary = summary,
                created = datetime.datetime.fromtimestamp(published_epoch_gmt),
                gist_id = gist_id,
                owner = user_info.key,
                slug = slug,
                article_type = article_type,
            )
            article.put()

            self.add_message(_('Article "%s" successfully created!' % title), 'success')
            return self.redirect_to('blog-article-list', username=username)
        else:
            # put_user_article call in models.py
            self.add_message(_('Article was not created.  Something went horribly wrong somewhere!' % name), 'warning')
            return self.get()
	
    @webapp2.cached_property
    def form(self):
        return forms.BlogArticleForm(self)


class BlogArticleListHandler(BaseHandler):
    @user_required
    def get(self, username=None):
        # pull the github token out of the social user db
        user_info = models.User.get_by_id(long(self.user_id))
        social_user = models.SocialUser.get_by_user_and_provider(user_info.key, 'github')

        # what do we do if we don't have a token or association?  auth 'em!
        if not social_user:
            scope = 'gist'
            # drop a short lived cookie so we know where to come back to when we're done auth'ing
            utils.write_cookie(self, 'oauth_return_url', 'user-profile', '/', 15)
            github_helper = github.GithubAuth(scope)
            self.redirect( github_helper.get_authorize_url() )
            return
        else:
            articles = models.Article.get_by_user(user_info.key)

            if not articles:
                # no articles, no problem, make one
                params = {'username': username}
                return self.redirect_to('blog-article-create', **params)
            else:
                # setup channel to do page refresh in case they sync
                channel_token = user_info.key.urlsafe()
                refresh_channel = channel.create_channel(channel_token)
                params = {
                    'articles': articles, 
                    'refresh_channel': refresh_channel, 
                    'channel_token': channel_token, 
                    'username': username
                }
                return self.render_template('blog/blog_article_list.html', **params)


###################
# Utility Methods #
###################
class BlogClearCacheHandler(BaseHandler):
    @user_required
    def get(self, article_id=None, username=None):
        user_info = models.User.get_by_id(long(self.user_id))
        article = models.Article.get_by_id(long(article_id))

        if article.owner == user_info.key and github.flush_gist_content(article.gist_id):
            message = 'Article was flushed from cache.'
        else:
            message = 'Something went wrong flushing from cache!'
        
        return message


# special class for routing menu in base.html template when user is logged in or logged out
# apparently using non-existant variables in uri_for() call (even when in a template if statement)
# causes some sort of appengine bug to manifest itself, causes it to crash hard, and leave
# a half-running zombie dev webserver process in place which has to be manually killed
class BlogUserMenuHandler(BaseHandler):
    @user_required
    def get(self, menu_id=None):
        user_info = models.User.get_by_id(long(self.user_id))

        if menu_id == 'newarticle':
            return self.redirect_to('blog-article-create', username=user_info.username)
        elif menu_id == 'myarticles':
            return self.redirect_to('blog-article-list', username=user_info.username)
        elif menu_id == 'mystack':
            return self.redirect_to('blog-user', username=user_info.username)
        else:
            return self.redirect_to('home')


# JOB SCHEDULER
# schedule a job request for rebuilding user's articles from their github gists
class BlogRefreshHandler(BaseHandler):
    def task(self, user=None, channel_token=None):
        # use both token and user to schedule job for updating user's articles from github gists
        params = {'channel_token': channel_token, 'user': user, 'job_token': config.job_token}
        t = taskqueue.add(method='GET', url='/blog/buildlist/', params=params, transactional=True)
        return

    # user pushed article refresh/rebuild button on article list page
    @user_required
    def get(self):
        # refresh_token gets passed in URL and we use the current logged in user to start a job
        channel_token = self.request.get('channel_token')
        user = self.user_id
        db.run_in_transaction(self.task, user, channel_token)
        return


# JOB HANDLER
# handle a job request for rebuilding a user's articles from their github gists
class BlogBuildListHandler(BaseHandler):
    def get(self):
        # pull the github token out of the social user db and grab gists from github
        if self.request.get('job_token') != config.job_token:
            logging.info("Hacker attack on jobs!")
            return
        else: 
            user_info = models.User.get_by_id(long(self.request.get('user')))
            social_user = models.SocialUser.get_by_user_and_provider(user_info.key, 'github')
            gists = github.get_user_gists(social_user.uid, social_user.access_token)

            # update with the gists
            for gist in gists:
                article = models.Article.get_by_user_and_gist_id(user_info.key, gist['gist_id'])

                if article:
                    # update existing article with new data
                    article.title = gist['title']
                    article.summary = gist['summary']
                    article.gist_id = gist['gist_id']
                    article.article_type = gist['article_type']
                    article.updated = datetime.datetime.fromtimestamp(gist['published'])
                else:
                    # we have a new article on our hands - insert
                    # prep the slug
                    slug = utils.slugify(gist['title'])
                    article = models.Article(
                        title = gist['title'],
                        summary = gist['summary'],
                        created = datetime.datetime.fromtimestamp(gist['published']),
                        gist_id = gist['gist_id'],
                        owner = user_info.key,
                        slug = slug,
                        article_type = gist['article_type'],
                    )
                
                # update
                article.put()

                # flush memcache copy just in case we had it
                github.flush_gist_content(article.gist_id)
                                
            # use the channel to tell the browser we are done
            channel_token = self.request.get('channel_token')
            channel.send_message(channel_token, 'reload')
            return

    def post(self):
        self.get()
