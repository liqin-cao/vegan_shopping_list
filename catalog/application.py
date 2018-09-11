#!/usr/bin/env python

from flask import Flask, render_template, request, redirect
from flask import jsonify, url_for, flash, make_response
from flask import session as login_session

from sqlalchemy import create_engine, asc
from sqlalchemy.orm import scoped_session
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.exc import NoResultFound
from db_models import Base, Category, Item, User

from oauth2client.client import flow_from_clientsecrets
from oauth2client.client import FlowExchangeError

import random
import string
import httplib2
import json
import bleach

from oauth_utils import authorized, gen_state_token
from oauth_utils import google_connect, facebook_connect, disconnect

app = Flask(__name__)
app.jinja_env.globals.update(gen_state_token=gen_state_token)

# Connect to Database and create database session
engine = create_engine('sqlite:///catalogWithOAuth.db')
Base.metadata.bind = engine
DB_session = scoped_session(sessionmaker(bind=engine))


# Registered a logged in user in the database.
def registerUser():
    # New user login, create a new user if the user doesn't exist.
    user_id = getUserID(login_session['email'])
    if not user_id:
        user_id = createUser()
    login_session['user_id'] = user_id

    response = make_response(
        json.dumps("You are now logged in as {}".format(
            login_session['username'])), 200)
    response.headers['Content-Type'] = 'application/json'
    return response


# Retrieve user id by email
def getUserID(email):
    try:
        user = DB_session.query(User).filter_by(email=email).one()
        return user.id
    except NoResultFound:
        return None


# Retrieve user info by id
def getUserInfo(user_id):
    try:
        user = DB_session.query(User).filter_by(id=user_id).one()
        return user
    except NoResultFound:
        return None


# Create a new user from login info
def createUser():
    newUser = User(
        name=login_session['username'],
        email=login_session['email'],
        picture=login_session['picture'])
    DB_session.add(newUser)
    DB_session.commit()
    try:
        user = DB_session.query(User).filter_by(
            email=login_session['email']).one()
        return user.id
    except NoResultFound:
        return None


@app.teardown_request
def remove_session(exception=None):
    # http://docs.sqlalchemy.org/en/rel_1_0/orm/contextual.html#using-thread-local-scope-with-web-applications
    # https://github.com/mitsuhiko/flask-sqlalchemy/issues/379
    #
    # The process of integrating the SQLAlchemy Session with the web
    # application has exactly two requirements:
    # 1. Create a single scoped_session registry when the web application
    # first starts, ensuring that this object is accessible by the rest of
    # the application.
    # 2. Ensure that scoped_session.remove() is called when the web request
    # ends, usually by integrating with the web framework's event system to
    # establish an "on request end" event.
    DB_session.remove()


# JSON API to view all catalog items in all categories
@app.route('/catalog.json')
def catalogJSON():
    '''Retrieve the entire catalog from database and
       return it as a single json object.'''
    categoriesJSON = []

    categories = DB_session.query(Category).order_by(asc(Category.name))
    for category in categories:
        items = DB_session.query(Item).filter_by(cat_id=category.id).all()
        categoryJSON = category.serialize
        categoryJSON['Item'] = [item.serialize for item in items]
        categoriesJSON.append(categoryJSON)

    return jsonify(Category=categoriesJSON)


# Login with Google oauth api
@app.route('/gconnect', methods=['POST'])
def gconnect():
    response = google_connect(request)
    if response is not None:
        # Login failure or user already logged in.
        return response

    return registerUser()


# Login with Facebook oauth api
@app.route('/fbconnect', methods=['POST'])
def fbconnect():
    response = facebook_connect(request)
    if response is not None:
        # Login failure or user already logged in.
        return response

    return registerUser()


# Logout based on provider
@app.route('/logout')
def logout():
    disconnect()
    return redirect(url_for('showCategories'))


@app.errorhandler(404)
def page_not_found(e):
    return render_template(
        'error.html',
        errormsg="404 What you were looking for is just not there."), 404


# Show error page
@app.route('/error')
def error():
    return render_template(
        'error.html',
        errormsg="""Oops... Something went wrong.  Please check
            the console and try again.""")


# Show home page: categories | latest items
@app.route('/')
def showCategories():
    categories = DB_session.query(Category).order_by(asc(Category.name))
    latestItems = DB_session.query(Item).order_by(
        asc(Item.created_date)).limit(categories.count())
    flash("Welcome!")
    return render_template(
        'catalog.html',
        leftPanel='category/categorylist.html',
        rightPanel='category/latestlist.html',
        categories=categories,
        latestItems=latestItems)


# Show categories | category items
@app.route('/catalog/<category_name>/items')
def showCategoryItems(category_name):
    category_id = request.args.get('category_id')
    if category_id is None:
        return page_not_found(None)

    items = DB_session.query(Item).filter_by(
        cat_id=category_id).order_by(asc(Item.title)).all()
    try:
        selectedCategory = DB_session.query(Category).filter_by(
            id=category_id).one()
    except NoResultFound:
        return page_not_found(None)

    categories = DB_session.query(Category).order_by(asc(Category.name))
    return render_template(
        'catalog.html',
        leftPanel='category/categorylist.html',
        rightPanel='category/itemlist.html',
        categories=categories,
        selectedCategory=selectedCategory,
        items=items)


# Show category item info
@app.route('/catalog/<category_name>/<item_title>')
def showCategoryItem(category_name, item_title):
    item_id = request.args.get('item_id')
    if item_id is None:
        return page_not_found(None)

    try:
        selectedCategoryItem = DB_session.query(Item).filter_by(
            id=item_id).one()
    except NoResultFound:
        return page_not_found(None)

    items = DB_session.query(Item).filter_by(
        cat_id=selectedCategoryItem.cat_id).order_by(asc(Item.title)).all()

    return render_template(
        'catalog.html',
        leftPanel='category/itemlist.html',
        rightPanel='category/item.html',
        selectedCategory=selectedCategoryItem.category,
        selectedCategoryItem=selectedCategoryItem,
        items=items)


# Add a new catalog/category item
@app.route('/catalog/new', methods=['GET', 'POST'])
def newCatalogItem():
    # Redirect to home page for unauthorized user.
    if not authorized():
        return redirect('/')

    if request.method == 'POST':
        # Create a new category item
        if request.form['title']:
            newItem = Item(
                title=bleach.clean(request.form['title']),
                description=bleach.clean(request.form['description']),
                cat_id=request.form['category'],
                user_id=login_session['user_id'])
            DB_session.add(newItem)
            DB_session.commit()
            flashmsg = "{} as been successfully added!".format(
                newItem.title)
            flash(flashmsg)
            return redirect(url_for(
                'showCategoryItem',
                category_name=newItem.category.urlname,
                item_title=newItem.urltitle,
                item_id=newItem.id))
        else:
            return render_template(
                'error.html',
                errormsg="Oops... Failed to create a new item.")
    else:
        # Show add category item page
        categories = DB_session.query(Category).order_by(asc(Category.name))
        category_id = request.args.get('category_id')
        if category_id is not None:
            try:
                selectedCategory = DB_session.query(Category).filter_by(
                    id=category_id).one()
            except NoResultFound:
                return page_not_found(None)

            return render_template(
                'catalog.html',
                leftPanel='category/categorylist.html',
                rightPanel='category/newitem.html',
                categories=categories,
                selectedCategory=selectedCategory)
        else:
            return render_template(
                'catalog.html',
                leftPanel='category/categorylist.html',
                rightPanel='category/newitem.html',
                categories=categories)


# Edit a category item
@app.route('/catalog/<item_title>/edit', methods=['GET', 'POST'])
def editCategoryItem(item_title):
    # Redirect to home page for unauthorized user.
    if not authorized():
        return redirect('/')

    item_id = request.args.get('item_id')
    if item_id is None:
        return page_not_found(None)

    try:
        editCategoryItem = DB_session.query(Item).filter_by(id=item_id).one()
    except NoResultFound:
        return page_not_found(None)

    if request.method == 'POST':
        # Update the category item.
        if request.form['title']:
            editCategoryItem.title = bleach.clean(request.form['title'])
        if request.form['description']:
            editCategoryItem.description = bleach.clean(
                request.form['description'])
        DB_session.add(editCategoryItem)
        DB_session.commit()
        flashmsg = "{} as been successfully updated!".format(
            editCategoryItem.title)
        flash(flashmsg)
        return redirect(url_for(
            'showCategoryItem',
            category_name=editCategoryItem.category.urlname,
            item_title=editCategoryItem.urltitle,
            item_id=editCategoryItem.id))
    else:
        # Show edit category item page
        items = DB_session.query(Item).filter_by(
            cat_id=editCategoryItem.cat_id).order_by(asc(Item.title)).all()
        return render_template(
            'catalog.html',
            leftPanel='category/itemlist.html',
            rightPanel='category/edititem.html',
            selectedCategory=editCategoryItem.category,
            selectedCategoryItem=editCategoryItem,
            items=items)


# Delete a category item
@app.route('/catalog/<item_title>/delete', methods=['GET', 'POST'])
def deleteCategoryItem(item_title):
    # Redirect to home page for unauthorized user.
    if not authorized():
        return redirect('/')

    item_id = request.args.get('item_id')
    if item_id is None:
        return page_not_found(None)

    try:
        deleteCategoryItem = DB_session.query(Item).filter_by(id=item_id).one()
    except NoResultFound:
        return page_not_found(None)

    if request.method == 'POST':
        # Delete the category item
        category = deleteCategoryItem.category
        DB_session.delete(deleteCategoryItem)
        DB_session.commit()
        flashmsg = "{} as been successfully deleted!".format(
            deleteCategoryItem.title)
        flash(flashmsg)
        return redirect(url_for(
            'showCategoryItems',
            category_name=category.urlname,
            category_id=category.id))
    else:
        # Show delete category item confirmation page
        items = DB_session.query(Item).filter_by(
            cat_id=deleteCategoryItem.cat_id).order_by(asc(Item.title)).all()
        return render_template(
            'catalog.html',
            leftPanel='category/itemlist.html',
            rightPanel='category/deleteitem.html',
            selectedCategory=deleteCategoryItem.category,
            selectedCategoryItem=deleteCategoryItem,
            items=items)


if __name__ == '__main__':
    app.secret_key = 'super_secret_key'
    app.debug = True

    # Set threaded to True to serve multiple clients concurrently
    # https://stackoverflow.com/questions/14814201/can-i-serve-multiple-clients-using-just-flask-app-run-as-standalone/14823968#14823968
    # app.run(host='0.0.0.0', port=8000, threaded=True)
    app.run(host='0.0.0.0', port=8000)
